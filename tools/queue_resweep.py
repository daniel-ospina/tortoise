#!/usr/bin/env python3
"""queue_resweep — re-request queue entry for the PRs that are already ready.

WHY THIS EXISTS
    ``merge_protections_settings.auto_merge_conditions: true`` is supposed to
    queue finished work with no click (#5424). But a PR's checks are evaluated
    against ``main ∪ the branch``, so while the required gate is RED on ``main``
    NO PR can satisfy its entry conditions and auto-queue is SILENTLY
    SUPPRESSED. Measured 2026-09-26: **0 of 21** newly-opened PRs auto-queued
    while ``python-ci-gate`` was red on ``main`` — the one that appeared queued
    had been nudged by hand.

    So ``0 queued`` reads as "nothing is ready" when it means "everything is
    ready and the gate is stuck". When ``main`` goes green, every ready PR needs
    exactly one ``@mergifyio queue`` comment — as an earlier 46 did, by hand,
    one at a time.

DESIGN CONTRACT
    1. DRY RUN BY DEFAULT. ``--live`` is required before a single comment is
       posted, and the run prints what it WOULD do. Nothing in this module
       writes until ``--live`` is passed.
    2. NEVER TOUCH A PR ALREADY IN THE QUEUE. A PR carrying the ``queued`` label
       or a Mergify check-run that is not merely "waiting" is skipped before any
       command is considered, whatever else is true about it.
    3. VERIFY THE ARTIFACT, NOT THE SEND. A 2xx from the comment POST means bytes
       were accepted — not that the PR entered the queue. The evidence is a
       **NEW** ``Mergify Merge Queue`` check-run: ``in_progress`` (the queue took
       it) or ``completed/success`` (the queue took it and its own check passed)
       both mean ENTERED, and BOTH require the run to be newer than every run that
       existed BEFORE the post — an old run is not evidence about a new command.
       A ``completed/neutral`` result means the PR is WAITING (an entry condition
       is unmet) and is NOT queued. No new check-run at all is UNKNOWN, and the
       run exits non-zero rather than calling it success.
    4. NAME THE UNMET CONDITION. Every PR that did not enter is reported with the
       specific condition that stopped it (``check-success=docs: completed/failure``,
       ``check-success=test-isolation: in_progress``, ``draft``, ...), never with
       a bare "failed". The most important case is the one that looks like "nothing
       is ready": the PR-side conditions are ALL met and the unmet one is on the
       BASE branch. That is the state §7 measured — measured 2026-09-26 on #5600:
       all six required checks ``completed/success`` on the head while ``main``'s
       ``python-ci-gate`` was ``completed/failure`` — so the tool reports
       ``SKIP-BASE-RED`` and names the base condition instead of pretending the
       PR is not ready, and refuses to post live until the base gate is green
       (each post would answer ``completed/neutral``) unless ``--allow-red-base``
       is passed deliberately.
    5. IDEMPOTENT. A ``@mergifyio queue`` comment created AFTER the head commit is
       a live command; re-posting it is noise and rate-limit spend. A push
       invalidates it, so the command is re-posted only once the head has moved.
    6. SPACING. Comments are posted with a delay (``--spacing``, default 3 s) so a
       ~90-PR sweep does not trip GitHub's secondary rate limit.
    7. THE ENTRY CONDITIONS COME FROM THE CONFIG, NOT FROM THIS FILE. They are
       parsed from ``.mergify.yml`` — by default from ``origin/main``, because
       that is the copy the live queue loaded. An unrecognised condition is a
       CONFIG ERROR (fail closed): silently ignoring a gate is how this tool
       would queue something the queue itself would refuse.

Usage
    python3 tools/queue_resweep.py                       # dry run, all candidates
    python3 tools/queue_resweep.py --only 5527 --only 5384
    python3 tools/queue_resweep.py --live                # ACTUALLY posts comments
    python3 tools/queue_resweep.py --live --allow-red-base   # post even while the base gate is red

Exit codes
    0  every candidate reached a terminal verdict (dry-run: every PR classified)
    1  a post failed, or an entry could not be verified (UNKNOWN)
    2  could not read the queue config, or could not enumerate the open PRs
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

#: The queue command Mergify accepts as a PR comment. `@mergifyio queue` targets
#: the default queue; `--command` overrides it for a named queue.
DEFAULT_COMMAND = "@mergifyio queue"

#: The check-run Mergify posts for queue entry. `in_progress` == ENTERED;
#: `completed/neutral` == WAITING (measured 2026-09-26 on #5527 vs #5593).
QUEUE_CHECK_NAME = "Mergify Merge Queue"
MERGIFY_APP_SLUG = "mergify"
#: Mergify's own label on a queued PR.
QUEUE_LABEL = "queued"

#: Safety cap so a bug cannot turn one invocation into a spam run.
MAX_POSTS_DEFAULT = 200


class ConfigError(RuntimeError):
    """The queue config could not be read, or carries a condition we cannot honour."""


class GhError(RuntimeError):
    """A `gh api` call failed."""


# ── configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QueueConfig:
    """The entry gate, as read from `.mergify.yml`."""

    queue_name: str
    base: str
    required_checks: tuple[str, ...]


def parse_config(text: str) -> QueueConfig:
    """Parse the entry gate out of a `.mergify.yml` document.

    Only the conditions this tool can actually evaluate are accepted. Anything
    else raises, deliberately: a condition we skipped would be a gate the tool
    queues past.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment fault
        raise ConfigError(f"PyYAML is required to read the queue config: {exc}") from exc

    try:
        doc = yaml.safe_load(text)
    except Exception as exc:  # any parse failure is a config error
        raise ConfigError(f"could not parse the queue config: {exc}") from exc
    if not isinstance(doc, dict):
        raise ConfigError("the queue config did not parse to a mapping")

    rules = doc.get("queue_rules") or []
    if not isinstance(rules, list) or not rules:
        raise ConfigError("no `queue_rules` in the queue config")
    rule = rules[0]
    if not isinstance(rule, dict):
        raise ConfigError("`queue_rules[0]` is not a mapping")

    name = str(rule.get("name") or "default")
    conditions = rule.get("queue_conditions") or []
    if not isinstance(conditions, list) or not conditions:
        raise ConfigError(f"queue rule {name!r} has no `queue_conditions`")

    base = "main"
    checks: list[str] = []
    for raw in conditions:
        cond = str(raw).strip()
        m = re.fullmatch(r"base=(\S+)", cond)
        if m:
            base = m.group(1)
            continue
        m = re.fullmatch(r"check-success=(\S+)", cond)
        if m:
            checks.append(m.group(1))
            continue
        if cond in ("-draft", "draft=false", "-draft=true"):
            continue
        raise ConfigError(
            f"unsupported queue condition {cond!r} in queue rule {name!r} — "
            "the tool cannot evaluate it and will not queue past it"
        )

    if not checks:
        raise ConfigError(f"queue rule {name!r} names no `check-success` conditions")
    return QueueConfig(queue_name=name, base=base, required_checks=tuple(checks))


def load_config_text(config: str | None, main_ref: str = "origin/main") -> str:
    """Read the queue config.

    Default source is `origin/main:.mergify.yml` — the copy the live queue loaded —
    not the working tree, which may be a stale branch. Falls back to the
    on-disk `.mergify.yml`. `--config -` reads stdin.
    """
    if config == "-":
        return sys.stdin.read()
    if config:
        return Path(config).read_text(encoding="utf-8")
    try:
        out = subprocess.run(
            ["git", "show", f"{main_ref}:.mergify.yml"],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    except OSError:  # pragma: no cover - git absent
        pass
    return Path(".mergify.yml").read_text(encoding="utf-8")


# ── check-run interpretation ─────────────────────────────────────────────────


def newest_by_name(check_runs: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group check-runs by NAME, keeping the newest attempt.

    Newest is decided by `id`, never by `started_at` (which is nullable), and
    NOT by `check_suite.id` (each re-run gets its own suite). A re-run ADDS a
    run; it does not clear one, so the newest is the only one that counts.
    """
    newest: dict[str, dict[str, Any]] = {}
    for run in check_runs:
        name = str(run.get("name") or "")
        if not name:
            continue
        cur = newest.get(name)
        if cur is None or _as_int(run.get("id")) > _as_int(cur.get("id")):
            newest[name] = run
    return newest


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def newest_mergify_check(check_runs: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest `Mergify Merge Queue` check-run, or None."""
    candidates = [
        r
        for r in check_runs
        if str(r.get("name") or "") == QUEUE_CHECK_NAME
        and str(((r.get("app") or {}).get("slug")) or "") == MERGIFY_APP_SLUG
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: _as_int(r.get("id")))


def queue_state(check_runs: Iterable[dict[str, Any]]) -> str:
    """`in_queue` | `waiting` | `absent`, from the newest Mergify check-run.

    `in_queue` is the SAFE side: it means do not touch. It covers a run still in
    flight and a run that already completed successfully. `waiting` is the state
    the corpus measured for an unmet entry condition (`completed/neutral`).
    """
    run = newest_mergify_check(check_runs)
    if run is None:
        return "absent"
    status = str(run.get("status") or "")
    if status != "completed":
        return "in_queue"
    conclusion = str(run.get("conclusion") or "")
    if conclusion == "success":
        return "in_queue"
    if conclusion == "neutral":
        return "waiting"
    return "waiting"


def unmet_conditions(
    pr: dict[str, Any],
    check_runs: Iterable[dict[str, Any]],
    cfg: QueueConfig,
) -> list[str]:
    """Name every entry condition this PR does not currently satisfy.

    An empty list means the PR satisfies the gate as the config states it. Only
    `completed/success` passes a `check-success` condition — a skipped or neutral
    check must not read as a pass (the config's own contract).
    """
    unmet: list[str] = []
    if bool(pr.get("draft")):
        unmet.append("draft")
    base_ref = str(((pr.get("base") or {}).get("ref")) or "")
    if base_ref != cfg.base:
        unmet.append(f"base={cfg.base} (actual {base_ref or 'unknown'})")

    newest = newest_by_name(check_runs)
    for check in cfg.required_checks:
        run = newest.get(check)
        if run is None:
            unmet.append(f"check-success={check}: no check-run")
            continue
        status = str(run.get("status") or "")
        if status != "completed":
            unmet.append(f"check-success={check}: {status or 'unknown'} (in flight)")
            continue
        conclusion = str(run.get("conclusion") or "")
        if conclusion != "success":
            unmet.append(f"check-success={check}: completed/{conclusion or 'null'}")
    return unmet


# ── command idempotency ──────────────────────────────────────────────────────


def _parse_iso(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        import datetime as _dt

        return _dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def has_live_command(
    comments: Sequence[dict[str, Any]],
    head_committed_at: Any,
    command: str = DEFAULT_COMMAND,
) -> bool:
    """Is there already a queue command that this head has not invalidated?

    A comment posted BEFORE the current head commit was written against a
    different tree, so a push invalidates it and the command is due again. A
    comment after the head commit is live and re-posting it is noise.
    """
    head_ts = _parse_iso(head_committed_at)
    for comment in comments:
        body = str(comment.get("body") or "")
        if command not in body:
            continue
        created = _parse_iso(comment.get("created_at"))
        if created is None:
            continue
        if head_ts is None or created >= head_ts:
            return True
    return False


# ── GitHub access ────────────────────────────────────────────────────────────


class Github(Protocol):  # pragma: no cover - protocol
    def list_pulls(self, repo: str, base: str) -> list[dict[str, Any]]: ...
    def list_check_runs(self, repo: str, sha: str) -> list[dict[str, Any]]: ...
    def list_comments(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def head_committed_at(self, repo: str, sha: str) -> str: ...
    def post_comment(self, repo: str, number: int, body: str) -> dict[str, Any]: ...
    def branch_head_sha(self, repo: str, branch: str) -> str: ...


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


class GhCli:
    """GitHub access through `gh api` — the same authenticated path the skills use."""

    def _json(self, args: list[str]) -> Any:
        proc = _run(["gh", "api", *args])
        if proc.returncode != 0:
            raise GhError(f"gh api {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GhError(f"gh api returned non-JSON: {exc}") from exc

    def _get_list(self, path: str, params: str = "", select: str = ".[]") -> list[dict[str, Any]]:
        """Every element, across pages. `--paginate` emits one object per page;
        `--jq '<select> | @json'` makes each ELEMENT one line, which is what we parse.

        `select` MUST end at the array to iterate, and MUST NOT be reused blindly:
        the check-runs endpoint returns an OBJECT (`{total_count, check_runs: [...]}`),
        so `.[]` yields that object's VALUES — a scalar and a nested list — not the
        check-runs. That mistake survived the hermetic tests and was caught by the
        first real dry run.
        """
        url = f"{path}?per_page=100" + (f"&{params}" if params else "")
        proc = _run(["gh", "api", "--paginate", url, "--jq", f"{select} | @json"])
        if proc.returncode != 0:
            raise GhError(f"gh api {url} failed: {proc.stderr.strip()[:300]}")
        out: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                element = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GhError(f"gh api {url} returned a non-JSON line: {exc}") from exc
            if not isinstance(element, dict):
                raise GhError(
                    f"gh api {url} selected a {type(element).__name__}, not an object — "
                    f"the selector {select!r} does not iterate the array this endpoint returns"
                )
            out.append(element)
        return out

    def list_pulls(self, repo: str, base: str) -> list[dict[str, Any]]:
        return self._get_list(f"repos/{repo}/pulls", f"state=open&base={base}")

    def list_check_runs(self, repo: str, sha: str) -> list[dict[str, Any]]:
        return self._get_list(
            f"repos/{repo}/commits/{sha}/check-runs", "filter=all", select=".check_runs[]"
        )

    def list_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._get_list(f"repos/{repo}/issues/{number}/comments")

    def _text(self, args: list[str]) -> str:
        """A single RAW jq value. `gh --jq` prints strings unquoted, so the result
        is not JSON (a date arrives as `2026-09-26T...`, not `"2026-09-26T..."`).
        Parsing it with `json.loads` failed on char 4 and misreported every such PR
        as UNKNOWN on the first real dry run.
        """
        proc = _run(["gh", "api", *args])
        if proc.returncode != 0:
            raise GhError(f"gh api {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout.strip()

    def head_committed_at(self, repo: str, sha: str) -> str:
        return self._text([f"repos/{repo}/commits/{sha}", "--jq", ".commit.committer.date"])

    def branch_head_sha(self, repo: str, branch: str) -> str:
        return self._text([f"repos/{repo}/branches/{branch}", "--jq", ".commit.sha"])

    def post_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        return self._json(
            [
                "-X",
                "POST",
                f"repos/{repo}/issues/{number}/comments",
                "-f",
                f"body={body}",
                "--jq",
                "{id: .id, url: .html_url}",
            ]
        )


# ── the sweep ────────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    number: int
    title: str
    verdict: str
    detail: str = ""
    check_id: int | None = None

    def line(self, width: int = 58) -> str:
        title = self.title if len(self.title) <= width else self.title[: width - 1] + "…"
        return f"#{self.number:<7} {title:<{width}} {self.verdict:<16} {self.detail}"


@dataclass
class SweepReport:
    repo: str
    dry_run: bool
    config: QueueConfig
    base_sha: str = ""
    base_unmet: list[str] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def base_green(self) -> bool:
        return not self.base_unmet

    def tally(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for o in self.outcomes:
            counts[o.verdict] = counts.get(o.verdict, 0) + 1
        return counts

    def render(self) -> str:
        gate = (
            "GREEN"
            if self.base_green
            else "RED — entry is closed for EVERY PR until it clears: " + "; ".join(self.base_unmet)
        )
        lines = [
            f"queue_resweep — repo {self.repo}, queue rule {self.config.queue_name!r}, "
            f"base {self.config.base}@{self.base_sha[:9] or '?'}, "
            f"required checks: {', '.join(self.config.required_checks)}",
            f"base gate: {gate}",
            "mode: " + ("DRY RUN (nothing posted)" if self.dry_run else "LIVE"),
            "",
        ]
        lines += [o.line() for o in self.outcomes] or ["(no open PRs matched)"]
        counts = self.tally()
        lines += [
            "",
            f"SUMMARY: {len(self.outcomes)} open PR(s) inspected — "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        ]
        if not self.base_green:
            lines.append(
                f"⚠️  The base gate is RED: {counts.get('SKIP-BASE-RED', 0)} PR(s) are "
                "PR-side ready but cannot enter. `0 queued` here does NOT mean nothing is ready — "
                "it means everything is ready and the gate is stuck. Re-run this tool once the "
                "base is green."
            )
        if self.dry_run:
            lines.append(
                f"{counts.get('WOULD-POST', 0)} PR(s) would receive `{DEFAULT_COMMAND}`; "
                f"{counts.get('SKIP-IN-QUEUE', 0)} already in the queue (untouched); "
                f"{counts.get('SKIP-UNMET', 0)} have a PR-side unmet condition (named above)."
            )
        else:
            lines.append(
                f"{counts.get('ENTERED', 0)} verified ENTERED (a NEW Mergify check-run "
                f"confirmed the queue took it); {counts.get('WAITING', 0)} are WAITING (the queue "
                f"said completed/neutral, which is NOT queued); {counts.get('UNKNOWN', 0)} could "
                f"not be verified; {counts.get('SKIP-LIVE-COMMAND', 0)} already had a live command."
            )
        return "\n".join(lines)


def base_gate(client: Github, repo: str, cfg: QueueConfig) -> tuple[str, list[str]]:
    """The base branch's own state against the entry conditions.

    This is the condition §7 measured but nothing reported: while the base gate is
    red, NO PR can meet its entry conditions, because Mergify evaluates them against
    `base ∪ branch`. Naming it separately is the difference between "nothing is
    ready" and "everything is ready and the gate is stuck".

    ONLY checks that actually REPORT a run on the base branch can be a base-side
    blocker. A required check with no check-run on the base head is one that does not
    run on a push (most of them run on `pull_request`, where they do report and are
    evaluated PR-side) — reporting it as an unmet BASE condition would make the gate
    permanently red and the tool permanently useless. Measured 2026-09-26 on
    `main@877fa52d1`: `python-ci-gate` `completed/failure`; the other five required
    contexts had no check-run at all.
    """
    sha = client.branch_head_sha(repo, cfg.base)
    checks = client.list_check_runs(repo, sha) if sha else []
    newest = newest_by_name(checks)
    unmet: list[str] = []
    for name in cfg.required_checks:
        run = newest.get(name)
        if run is None:
            continue  # does not report on the base branch — not a base-side gate
        status = str(run.get("status") or "")
        if status != "completed":
            unmet.append(f"check-success={name}: {status or 'unknown'} (in flight on {cfg.base})")
            continue
        conclusion = str(run.get("conclusion") or "")
        if conclusion != "success":
            unmet.append(f"check-success={name}: completed/{conclusion or 'null'}")
    return sha, unmet


def run_sweep(
    client: Github,
    repo: str,
    cfg: QueueConfig,
    *,
    dry_run: bool = True,
    only: Sequence[int] = (),
    command: str = DEFAULT_COMMAND,
    spacing: float = 3.0,
    post_cap: int = MAX_POSTS_DEFAULT,
    poll_timeout: float = 90.0,
    poll_interval: float = 6.0,
    allow_red_base: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> SweepReport:
    """Classify every candidate, and (when live) post and VERIFY the command.

    Nothing is posted in dry-run mode. A PR in the queue is never posted to. While
    the BASE gate is red, no post is made unless ``allow_red_base`` is set — every
    one of them would answer ``completed/neutral`` (waiting), which is the state the
    re-sweep exists to recover from once the base is green.
    """
    base_sha, base_unmet = base_gate(client, repo, cfg)
    report = SweepReport(
        repo=repo, dry_run=dry_run, config=cfg, base_sha=base_sha, base_unmet=base_unmet
    )
    pulls = client.list_pulls(repo, cfg.base)
    if only:
        wanted = set(only)
        pulls = [p for p in pulls if int(p.get("number") or 0) in wanted]

    posted = 0
    for pr in pulls:
        number = int(pr.get("number") or 0)
        title = str(pr.get("title") or "")
        head_sha = str(((pr.get("head") or {}).get("sha")) or "")
        labels = {str((lb or {}).get("name") or "") for lb in (pr.get("labels") or [])}
        checks = client.list_check_runs(repo, head_sha) if head_sha else []

        # (2) never touch a PR already in the queue.
        state = queue_state(checks)
        if QUEUE_LABEL in labels or state == "in_queue":
            why = f"label {QUEUE_LABEL!r}" if QUEUE_LABEL in labels else "Mergify check not waiting"
            report.outcomes.append(Outcome(number, title, "SKIP-IN-QUEUE", why))
            continue

        # (4) name the unmet condition — PR side first, then the base gate.
        unmet = unmet_conditions(pr, checks, cfg)
        if unmet:
            report.outcomes.append(Outcome(number, title, "SKIP-UNMET", "; ".join(unmet)))
            continue
        if base_unmet and not allow_red_base:
            report.outcomes.append(
                Outcome(
                    number,
                    title,
                    "SKIP-BASE-RED",
                    "PR-side conditions met; base gate unmet: " + "; ".join(base_unmet),
                )
            )
            continue

        # (5) idempotency — a live command for THIS head already exists.
        comments = client.list_comments(repo, number)
        try:
            head_ts = client.head_committed_at(repo, head_sha)
        except GhError as exc:
            report.outcomes.append(
                Outcome(number, title, "UNKNOWN", f"head commit unreadable: {exc}")
            )
            continue
        if has_live_command(comments, head_ts, command):
            report.outcomes.append(
                Outcome(
                    number, title, "SKIP-LIVE-COMMAND", f"{command!r} already posted for this head"
                )
            )
            continue

        if dry_run:
            report.outcomes.append(
                Outcome(
                    number,
                    title,
                    "WOULD-POST",
                    f"all {len(cfg.required_checks)} required checks success",
                )
            )
            continue

        if posted >= post_cap:
            report.outcomes.append(
                Outcome(number, title, "SKIP-CAP", f"post cap {post_cap} reached")
            )
            continue

        before_id = _as_int((newest_mergify_check(checks) or {}).get("id"))
        if posted:  # spacing BETWEEN posts, never after the last
            sleep(spacing)
        try:
            client.post_comment(repo, number, command)
        except GhError as exc:
            report.outcomes.append(Outcome(number, title, "POST-FAILED", str(exc)))
            continue
        posted += 1

        # (3) verify the ARTIFACT — a NEW in_progress Mergify check-run.
        def _describe(ch: list[dict[str, Any]], _pr: dict[str, Any] = pr) -> str:
            return _describe_unmet(_pr, ch, cfg, base_unmet)

        verdict, detail, check_id = _verify_entry(
            client,
            repo,
            head_sha,
            before_id,
            timeout=poll_timeout,
            interval=poll_interval,
            now=now,
            sleep=sleep,
            describe_unmet=_describe,
        )
        report.outcomes.append(Outcome(number, title, verdict, detail, check_id))

    return report


def _describe_unmet(
    pr: dict[str, Any],
    checks: Iterable[dict[str, Any]],
    cfg: QueueConfig,
    base_unmet: Sequence[str],
) -> str:
    """Which condition is actually unmet, given the queue answered "waiting"."""
    own = unmet_conditions(pr, checks, cfg)
    if own:
        return "; ".join(own)
    if base_unmet:
        return "PR-side conditions all met; base gate unmet: " + "; ".join(base_unmet)
    return "no unmet condition found — the queue answered neutral anyway"


def _verify_entry(
    client: Github,
    repo: str,
    head_sha: str,
    before_id: int,
    *,
    timeout: float,
    interval: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
    describe_unmet: Callable[[list[dict[str, Any]]], str] | None = None,
) -> tuple[str, str, int | None]:
    """Poll for a NEW Mergify check-run and read its state.

    UNKNOWN is a first-class verdict: "the comment was accepted" is not evidence
    that the PR entered, and reporting it as entered is the exact failure this
    tool exists to avoid.
    """
    deadline = now() + timeout
    last: dict[str, Any] | None = None
    while True:
        try:
            checks = client.list_check_runs(repo, head_sha)
        except GhError as exc:
            return "UNKNOWN", f"could not re-read check-runs: {exc}", None
        run = newest_mergify_check(checks)
        if run is not None and _as_int(run.get("id")) > before_id:
            last = run
            status = str(run.get("status") or "")
            conclusion = str(run.get("conclusion") or "")
            if status == "in_progress":
                return "ENTERED", "new Mergify check-run is in_progress", _as_int(run.get("id"))
            if status == "completed":
                why = (
                    describe_unmet(checks) if describe_unmet else ""
                ) or "unmet condition unnamed"
                if conclusion == "neutral":
                    return (
                        "WAITING",
                        f"queue answered completed/neutral — waiting, NOT queued; {why}",
                        _as_int(run.get("id")),
                    )
                if conclusion == "success":
                    return "ENTERED", f"queue check completed/{conclusion}", _as_int(run.get("id"))
                return (
                    "WAITING",
                    f"queue check completed/{conclusion or 'null'} — not a verified entry; {why}",
                    _as_int(run.get("id")),
                )
        if now() >= deadline:
            if last is None:
                return "UNKNOWN", f"no new Mergify check-run within {timeout:.0f}s", None
            return (
                "UNKNOWN",
                f"Mergify check-run stuck at {last.get('status')}",
                _as_int(last.get("id")),
            )
        sleep(interval)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", required=True, help="owner/name (e.g. daniel-ospina/tortoise)")
    parser.add_argument(
        "--config",
        default=None,
        help="queue config path, or '-' for stdin (default: origin/main:.mergify.yml, then ./.mergify.yml)",
    )
    parser.add_argument(
        "--live", action="store_true", help="ACTUALLY post the queue command (default: dry run)"
    )
    parser.add_argument(
        "--allow-red-base",
        action="store_true",
        help="post even while the base gate is red (every post will answer completed/neutral until it clears)",
    )
    parser.add_argument(
        "--only", type=int, action="append", default=[], help="restrict to this PR (repeatable)"
    )
    parser.add_argument(
        "--command",
        default=DEFAULT_COMMAND,
        help=f"the comment to post (default {DEFAULT_COMMAND!r})",
    )
    parser.add_argument(
        "--spacing", type=float, default=3.0, help="seconds between posts (default 3)"
    )
    parser.add_argument(
        "--post-cap", type=int, default=MAX_POSTS_DEFAULT, help="maximum comments to post"
    )
    parser.add_argument(
        "--poll-timeout", type=float, default=90.0, help="seconds to wait for the queue check-run"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=6.0, help="seconds between verification polls"
    )
    args = parser.parse_args(argv)

    try:
        cfg = parse_config(load_config_text(args.config))
    except (ConfigError, OSError) as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        report = run_sweep(
            GhCli(),
            args.repo,
            cfg,
            dry_run=not args.live,
            only=args.only,
            command=args.command,
            spacing=args.spacing,
            post_cap=args.post_cap,
            poll_timeout=args.poll_timeout,
            poll_interval=args.poll_interval,
            allow_red_base=args.allow_red_base,
        )
    except GhError as exc:
        print(f"QUERY FAILED: {exc}", file=sys.stderr)
        return 2

    print(report.render())
    counts = report.tally()
    if counts.get("POST-FAILED") or counts.get("UNKNOWN") or counts.get("SKIP-CAP"):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
