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
  anything else                              → relay to intake, then label,
                                               comment, close

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
  * RELAY-THEN-CLOSE. The intake POST happens first. If it fails, the item is
    left OPEN, unlabelled and uncommented, so the next run retries it — and the
    run exits 1, so a dead intake is an alarm rather than a silent "handled".
  * COMPLETENESS. Every listing page must succeed. An unreadable page is not an
    empty page: a partial read aborts the whole run BEFORE any mutation (exit 2).
    This is the "never bulk-close on a rate-limited read" rule.
  * NOT-CONFIGURED fails closed. No `INBOUND_URL` or no `INBOUND_SECRET_*` means
    the record cannot be written, so nothing is closed (exit 2).
  * A bounded WINDOW (`--since-hours`, default 24) and a bounded run
    (`--max`, default 20). The window is what keeps a first run from reaching
    back through history; the cap reports the remainder instead of hiding it.
  * `--dry-run` prints the plan and performs nothing.

It carries no merge capability at all: it closes and relays, and it never
merges (#8375's property for `ci_check_repair.py`, kept here).

EXIT CODES
  0  complete: every selected item was relayed (or the run was a dry run)
  1  at least one item's intake POST failed — that item was left untouched
  2  could not act: intake not configured, or the listing was incomplete
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
MERGIFY = REPO_ROOT / ".mergify.yml"

#: Mergify's documented default `queue_branch_prefix` (the repo sets none).
DOCUMENTED_DEFAULT_PREFIX = "mergify/merge-queue/"
#: The idempotence marker and the label the event path applies. Bound to
#: `.github/workflows/inbound-relay.yml` by a test, never copied by hand.
LABEL = "queued-for-triage"
OWNER = "daniel-ospina"
DEFAULT_REPO = f"{OWNER}/tortoise"

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


# ── the sweep ───────────────────────────────────────────────────────────────


class Client(Protocol):
    def list_open(self, *, stop_before: datetime | None = None) -> list[dict[str, Any]]: ...
    def pull_detail(self, number: int) -> dict[str, Any]: ...
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
    dry_run: bool = False,
) -> Report:
    report = Report(dry_run=dry_run)

    if not cfg.intake_url or not cfg.intake_secret:
        report.errors.append(
            "intake is not configured (INBOUND_URL / INBOUND_SECRET_GITHUB): "
            "nothing is closed without a record"
        )
        return report

    cutoff = now - timedelta(hours=since_hours)
    try:
        listing = list(client.list_open(stop_before=cutoff))
        candidates = [
            item for item in listing if _created_at(item) >= cutoff and _prefilter(item, cfg)
        ]
        # `/issues` carries a PR without its head/base; the detail is what tells a
        # queue batch from an external PR. Fetched per candidate (a handful per
        # run) rather than paging the whole `/pulls` backlog, which is both slow
        # (a 60 s timeout is a real measurement, not a hypothetical) and heavy.
        items = [
            client.pull_detail(int(item["number"])) if "pull_request" in item else item
            for item in candidates
        ]
    except Exception as exc:  # any read failure is "incomplete"
        report.errors.append(f"listing incomplete, no action taken: {exc}")
        return report

    selected = [item for item in items if should_relay(item, cfg)]
    # Oldest first: the longest-waiting item is the one whose absence hurts.
    selected.sort(key=_created_at)
    report.selected = [(_kind(item), int(item["number"])) for item in selected]

    actionable = selected[: max(0, max_actions)]
    report.deferred = len(selected) - len(actionable)

    for item in actionable:
        kind, number = _kind(item), int(item["number"])
        if dry_run:
            report.relayed.append((kind, number))
            continue
        try:
            client.post_intake(intake_payload(item))
        except Exception as exc:
            report.failed.append((kind, number))
            report.notes.append(f"{kind} #{number}: intake POST failed: {exc}")
            continue
        client.add_label(number, cfg.label)
        client.comment(number, "\n".join(_COMMENT_PULL if kind == "pr" else _COMMENT_ISSUE))
        client.close(kind, number)
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
    """

    def __init__(self, repo: str, *, per_page: int = 100, max_pages: int = 10) -> None:
        self.repo = repo
        self.per_page = per_page
        self.max_pages = max_pages

    def _paginate(self, path: str, *, stop_before: datetime | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
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
        raise ListingError(
            f"{path}: more than {self.max_pages * self.per_page} items newer than "
            "the window — the listing is incomplete, so nothing may be closed"
        )

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
    report = sweep(
        GhClient(args.repo),
        cfg,
        now=datetime.now(UTC),
        since_hours=args.since_hours,
        max_actions=args.max_actions,
        dry_run=args.dry_run,
    )

    verb = "WOULD relay" if args.dry_run else "relayed"
    for kind, number in report.relayed:
        print(f"{verb} {kind} #{number}")
    for kind, number in report.failed:
        print(f"FAILED   {kind} #{number} (left open and unlabelled)")
    for note in report.notes:
        print(f"note: {note}", file=sys.stderr)
    if report.deferred:
        print(f"deferred {report.deferred} item(s) past --max={args.max_actions}")
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)
    print(
        f"sweep: selected={len(report.selected)} {verb}={len(report.relayed)} "
        f"failed={len(report.failed)} deferred={report.deferred}"
    )
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
