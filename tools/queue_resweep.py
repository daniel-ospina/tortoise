#!/usr/bin/env python3
"""queue_resweep — re-request queue entry for the PRs that are already ready.

WHAT THIS DOES
    Posts ONE ``@mergifyio queue`` comment per open non-draft PR that already
    satisfies the entry gate, then verifies that the PR actually entered, and
    reports the specific condition for every PR that did not.

WHY IT EXISTS (MEASURED, not inferred)
    2026-09-26, on `daniel-ospina/tortoise`:
      * `0 of 21` newly-opened PRs auto-queued while `check-success=python-ci-gate`
        was failing on `main`;
      * a dry run found **28** open PRs whose own six required checks were all
        ``completed/success`` and which were NOT in the queue (verified by hand on
        #5600: six green checks, and ``python-ci-gate`` ``completed/failure`` on
        ``main@877fa52d1``).
    So ``0 queued`` read as "nothing is ready" when it meant "everything is ready
    and the entry gate is not open". The recovery is one comment per ready PR, as
    an earlier 46 were done by hand.

    ⛔ This docstring deliberately records NO mechanism for the suppression.
    ``auto_merge_conditions`` (#5424) and the injection-mode change in #5384 own
    that question, and a process narration here would re-stale against them. What
    this tool assumes is narrow and checkable: the entry conditions live in
    ``queue_rules[0].queue_conditions`` in ``.mergify.yml``, and it evaluates
    exactly those, from the copy of the file ``origin/main`` carries.

WHAT IT DOES NOT DO
    It does not consult the review record. The head-bound review attestation is
    enforced only by LOCAL tooling (``review-enforcer``, ``scripts/atomic-land.sh``);
    nothing on the server-side path reads it (#5426, #5433). This tool posts the
    same comment any human would, so it adds no gate and removes none — and it does
    not pretend otherwise.

DESIGN CONTRACT
    1. DRY RUN BY DEFAULT. ``--live`` is required before a single comment is
       posted, and the run always prints what it would do and which commit it is
       acting on.
    2. NEVER TOUCH A PR ALREADY IN THE QUEUE. A PR carrying the ``queued`` label,
       or whose newest ``Mergify Merge Queue`` check-run shows the queue holds it
       (any non-``completed`` status, or ``completed/success``), is skipped before
       any command is considered. A completed run that is neither of those (a
       failed, cancelled or stale attempt) means the queue does NOT hold the PR,
       so the PR is a candidate — that is the recovery this tool performs, and
       ``queue_state`` documents the mapping.
    3. VERIFY THE ARTIFACT, NOT THE SEND. A 2xx from the comment POST means bytes
       were accepted — not that the PR entered the queue. The evidence is a
       **NEW** ``Mergify Merge Queue`` check-run (strictly newer by ``id`` than
       every run that existed before the post): ``in_progress`` (the queue took
       it) or ``completed/success`` (it took it and its own check passed) both
       mean ENTERED. ``completed/neutral`` means WAITING — an entry condition is
       unmet and the PR is NOT queued. No new check-run at all is UNKNOWN. The run
       exits non-zero for UNKNOWN rather than calling it success.
    4. NAME THE UNMET CONDITION. Every PR that did not enter carries the specific
       condition that stopped it (``check-success=docs: completed/failure``,
       ``check-success=test-isolation: in_progress (in flight)``, ``draft``, ...)
       and not a bare "failed". The most important case is the one that looks like
       "nothing is ready": the PR-side conditions are ALL met and the unmet one is
       on the BASE branch. That is reported as ``SKIP-BASE-RED`` with the base
       condition named, and live posting is refused while the base gate is red
       (each post would answer ``completed/neutral``) unless ``--allow-red-base``
       is passed deliberately.
    5. IDEMPOTENT. A ``@mergifyio queue`` comment created at or after the head
       commit is a live command, and re-posting it is noise and rate-limit spend.
       A push invalidates it, so the command is re-posted only once the head has
       moved. ``--repost-waiting`` relaxes this for the ONE case the default rule
       cannot distinguish: a command whose attempt already resolved to
       ``completed/neutral``. The default is off, because whether Mergify still
       tracks that request is not something this tool can observe.
    6. SPACING. Comments are posted with a delay (``--spacing``, default 3 s) so a
       ~90-PR sweep does not trip GitHub's secondary rate limit, and the total is
       bounded by ``--post-cap``.
    7. THE ENTRY CONDITIONS COME FROM THE CONFIG, NOT FROM THIS FILE. An
       unrecognised condition is a CONFIG ERROR (fail closed): silently ignoring a
       gate is how this tool would queue something the queue itself would refuse.
       A config error names the condition and exits 2, and the report says which
       copy of the config was read.
    8. THE REPORT NAMES ITS SCOPE. Repo, config source, base ref and head SHA, the
       command being posted, and any ``--only`` filter all appear in the output —
       a verdict that does not name what it measured cannot be trusted.

Usage
    python3 tools/queue_resweep.py --repo owner/name             # dry run, all candidates
    python3 tools/queue_resweep.py --repo owner/name --only 5527
    python3 tools/queue_resweep.py --repo owner/name --live      # ACTUALLY posts comments
    ... --live --allow-red-base      # post while the base gate is red (all will WAIT)
    ... --live --repost-waiting      # re-request for PRs whose last attempt answered neutral

Exit codes
    0  every candidate reached a terminal verdict (dry-run: every PR classified)
    1  a post failed, a PR could not be queried mid-sweep, an entry could not be
       verified (UNKNOWN), or the post cap was reached (the sweep is incomplete)
    2  the queue config could not be read, or the open PRs could not be enumerated
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

#: A required status context is `(app, name)`; the config names only the name.
#: The repo's required contexts are all reported by this app, so it is preferred
#: when resolving one — see `newest_by_name`.
REQUIRED_CHECK_APP = "github-actions"

#: Safety cap so a bug cannot turn one invocation into a spam run.
MAX_POSTS_DEFAULT = 200

#: Bound a single `gh` call so a hung network read cannot block a sweep forever.
GH_TIMEOUT_SECONDS = 60.0


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


def load_config_text(config: str | None, main_ref: str = "origin/main") -> tuple[str, str]:
    """Read the queue config, and say WHICH COPY was read.

    Default source is `origin/main:.mergify.yml` — the copy the live queue loaded —
    not the working tree, which may be a stale branch. The fallback to the on-disk
    file is reported rather than silent: which gate was evaluated is part of the
    verdict. `--config -` reads stdin.
    """
    if config == "-":
        return sys.stdin.read(), "<stdin>"
    if config:
        return Path(config).read_text(encoding="utf-8"), config
    try:
        out = subprocess.run(
            ["git", "show", f"{main_ref}:.mergify.yml"],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout, f"{main_ref}:.mergify.yml"
    except (OSError, subprocess.SubprocessError):
        pass
    path = Path(".mergify.yml")
    print(
        f"⚠️  could not read {main_ref}:.mergify.yml — falling back to {path} "
        "(this may be an unrelated branch's copy of the gate)",
        file=sys.stderr,
    )
    return path.read_text(encoding="utf-8"), f"{path} (FALLBACK — {main_ref} unreadable)"


# ── check-run interpretation ─────────────────────────────────────────────────


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _app_slug(run: dict[str, Any]) -> str:
    return str(((run.get("app") or {}).get("slug")) or "")


def check_apps(check_runs: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """Which app slugs report each check name — the ambiguity `newest_by_name` resolves."""
    apps: dict[str, set[str]] = {}
    for run in check_runs:
        name = str(run.get("name") or "")
        if name:
            apps.setdefault(name, set()).add(_app_slug(run))
    return apps


def newest_by_name(check_runs: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group check-runs by NAME, keeping the newest attempt.

    Newest is decided by `id`, never by `started_at` (which is nullable), and NOT by
    `check_suite.id` (each re-run gets its own suite). A re-run ADDS a run; it does
    not clear one, so the newest is the only one that counts.

    A required status context is `(app, name)` but the config names only the name,
    so a run from `REQUIRED_CHECK_APP` is preferred over a same-named run from any
    other app — a PR author who names a job after a required context must not be
    able to mask it. When no run comes from that app, the newest run from any app is
    used rather than going dark, and `check_apps` exposes the ambiguity.
    """
    newest: dict[str, dict[str, Any]] = {}
    for run in check_runs:
        name = str(run.get("name") or "")
        if not name:
            continue
        cur = newest.get(name)
        if cur is None:
            newest[name] = run
            continue
        run_preferred = _app_slug(run) == REQUIRED_CHECK_APP
        cur_preferred = _app_slug(cur) == REQUIRED_CHECK_APP
        if run_preferred != cur_preferred:
            newest[name] = run if run_preferred else cur
        elif _as_int(run.get("id")) > _as_int(cur.get("id")):
            newest[name] = run
    return newest


def newest_mergify_check(check_runs: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest `Mergify Merge Queue` check-run, or None."""
    candidates = [
        r
        for r in check_runs
        if str(r.get("name") or "") == QUEUE_CHECK_NAME and _app_slug(r) == MERGIFY_APP_SLUG
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: _as_int(r.get("id")))


def queue_state(check_runs: Iterable[dict[str, Any]]) -> str:
    """`in_queue` | `waiting` | `absent`, from the newest Mergify check-run.

    `in_queue` is the SAFE side — do not touch. It covers a run still in flight (any
    non-``completed`` status) and a run that already completed successfully.

    `waiting` is every OTHER completed conclusion, and it means only "the queue does
    not currently hold this PR": `neutral` is the measured state for an unmet entry
    condition, and `failure`/`cancelled`/`stale`/`timed_out`/null are failed or
    abandoned attempts. All of them leave the PR a candidate, which is the state the
    re-sweep exists to recover — `test_queue_state_*` pins the mapping.
    """
    run = newest_mergify_check(check_runs)
    if run is None:
        return "absent"
    status = str(run.get("status") or "")
    if status != "completed":
        return "in_queue"
    return "in_queue" if str(run.get("conclusion") or "") == "success" else "waiting"


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
    comment at or after the head commit is live, and re-posting it is noise.

    The command must stand ALONE on a line, not merely appear in prose. Mergify
    acts on a comment whose body IS the command; a comment that merely mentions it
    (an audit note, a lane's write-up) is not a queue request, and treating one as
    live would silently suppress the re-request this tool exists to make.

    This cannot tell "Mergify is still tracking that request" from "that request
    already resolved to `completed/neutral` and was never followed up" — the two
    look identical from here. The default (treat it as live) cannot cause a missed
    entry through spam, and `--repost-waiting` is the deliberate opt-in for the
    operator who reads a `neutral` attempt as one that needs re-requesting.
    """
    head_ts = _parse_iso(head_committed_at)
    wanted = command.strip().casefold()
    for comment in comments:
        body = str(comment.get("body") or "")
        if not any(line.strip().casefold() == wanted for line in body.splitlines()):
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
    """One `gh` invocation, bounded.

    A missing `gh` or a hung network read must surface as `GhError` (the exit-2
    class), never as an uncaught `FileNotFoundError` traceback and never as an
    indefinite block.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"{' '.join(args[:3])} timed out after {GH_TIMEOUT_SECONDS:.0f}s") from exc
    except OSError as exc:
        raise GhError(f"could not run {args[0]!r}: {exc}") from exc


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

    def head_committed_at(self, repo: str, sha: str) -> str:
        return self._text([f"repos/{repo}/commits/{sha}", "--jq", ".commit.committer.date"])

    def branch_head_sha(self, repo: str, branch: str) -> str:
        return self._text([f"repos/{repo}/branches/{branch}", "--jq", ".commit.sha"])

    def post_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        # `-f` (raw field), not `-F` (which would treat a leading @ as a FILE).
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


def _clean(text: Any, limit: int = 58) -> str:
    """A single printable line: PR titles are remote input and a title carrying
    control characters or ANSI escapes must not reach the operator's terminal."""
    flat = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    flat = " ".join(flat.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@dataclass
class Outcome:
    number: int
    title: str
    verdict: str
    detail: str = ""
    check_id: int | None = None

    def line(self, width: int = 58) -> str:
        return f"#{self.number:<7} {_clean(self.title, width):<{width}} {self.verdict:<16} {self.detail}"


@dataclass
class SweepReport:
    repo: str
    dry_run: bool
    config: QueueConfig
    command: str = DEFAULT_COMMAND
    config_source: str = ""
    base_sha: str = ""
    base_unmet: list[str] = field(default_factory=list)
    base_observable: bool = True
    only: Sequence[int] = ()
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def base_green(self) -> bool:
        return not self.base_unmet

    def tally(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for o in self.outcomes:
            counts[o.verdict] = counts.get(o.verdict, 0) + 1
        return counts

    def _gate_line(self) -> str:
        if not self.base_observable:
            return (
                "NOT OBSERVABLE — no required entry check reports a run on "
                f"{self.config.base} (the gate may be red or green; this tool cannot tell)"
            )
        if self.base_green:
            return "GREEN"
        return "RED — entry is closed for EVERY PR until it clears: " + "; ".join(self.base_unmet)

    def render(self) -> str:
        scope = f", scope --only {','.join(str(n) for n in self.only)}" if self.only else ""
        lines = [
            f"queue_resweep — repo {self.repo}, queue rule {self.config.queue_name!r}, "
            f"base {self.config.base}@{self.base_sha[:9] or '?'}{scope}",
            f"config: {self.config_source} — required checks: "
            f"{', '.join(self.config.required_checks)}",
            f"command: {self.command!r}",
            f"base gate: {self._gate_line()}",
            "mode: " + ("DRY RUN (nothing posted)" if self.dry_run else "LIVE"),
            "",
        ]
        lines += [o.line() for o in self.outcomes] or ["(no open PRs matched)"]
        counts = self.tally()
        lines += [
            "",
            f"SUMMARY: {len(self.outcomes)} PR(s) inspected{scope} — "
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        ]
        if self.base_observable and not self.base_green:
            lines.append(
                f"⚠️  The base gate is RED: {counts.get('SKIP-BASE-RED', 0)} PR(s) are "
                "PR-side ready but cannot enter. `0 queued` here does NOT mean nothing is ready — "
                "it means everything is ready and the gate is stuck. Re-run this tool once the "
                "base is green."
            )
        if self.dry_run:
            lines.append(
                f"{counts.get('WOULD-POST', 0)} PR(s) would receive `{self.command}`; "
                f"{counts.get('SKIP-IN-QUEUE', 0)} already in the queue (untouched); "
                f"{counts.get('SKIP-UNMET', 0)} have a PR-side unmet condition (named above)."
            )
        else:
            lines.append(
                f"{counts.get('ENTERED', 0)} verified ENTERED (a NEW Mergify check-run "
                f"confirmed the queue took it); {counts.get('WAITING', 0)} are WAITING (the queue "
                f"said completed/neutral, which is NOT queued); {counts.get('UNKNOWN', 0)} could "
                f"not be verified; {counts.get('QUERY-FAILED', 0)} could not be read; "
                f"{counts.get('SKIP-LIVE-COMMAND', 0)} already had a live command."
            )
        return "\n".join(lines)


def base_gate(client: Github, repo: str, cfg: QueueConfig) -> tuple[str, list[str], bool]:
    """The base branch's own state, and whether it is observable at all.

    ONLY checks that actually REPORT a run on the base branch can be a base-side
    blocker. A required check with no check-run on the base head is one that does
    not run on a push (most run on `pull_request`, where they do report and are
    evaluated PR-side) — reporting it as an unmet BASE condition would make the gate
    permanently red and the tool permanently useless. Measured 2026-09-26 on
    `main@877fa52d1`: `python-ci-gate` `completed/failure`; the other five required
    contexts had no check-run at all.

    `observable` is False when NO required check reports on the base — the honest
    answer is then "cannot tell", never a bare "GREEN". That is also the regime
    change #5384 would introduce by moving the heavy check out of
    `queue_conditions`, and the report names it rather than silently losing the
    feature.
    """
    sha = client.branch_head_sha(repo, cfg.base)
    checks = client.list_check_runs(repo, sha) if sha else []
    newest = newest_by_name(checks)
    reported = [name for name in cfg.required_checks if newest.get(name) is not None]
    unmet: list[str] = []
    for name in reported:
        run = newest[name]
        status = str(run.get("status") or "")
        if status != "completed":
            unmet.append(f"check-success={name}: {status or 'unknown'} (in flight on {cfg.base})")
            continue
        conclusion = str(run.get("conclusion") or "")
        if conclusion != "success":
            unmet.append(f"check-success={name}: completed/{conclusion or 'null'}")
    return sha, unmet, bool(reported)


def run_sweep(
    client: Github,
    repo: str,
    cfg: QueueConfig,
    *,
    dry_run: bool = True,
    only: Sequence[int] = (),
    command: str = DEFAULT_COMMAND,
    config_source: str = "",
    spacing: float = 3.0,
    post_cap: int = MAX_POSTS_DEFAULT,
    poll_timeout: float = 90.0,
    poll_interval: float = 6.0,
    allow_red_base: bool = False,
    repost_waiting: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> SweepReport:
    """Classify every candidate, and (when live) post and VERIFY the command.

    Nothing is posted in dry-run mode. A PR in the queue is never posted to. While
    the BASE gate is red, no post is made unless ``allow_red_base`` is set — every
    one of them would answer ``completed/neutral`` (waiting), which is the state the
    re-sweep exists to recover from once the base is green.
    """
    report = SweepReport(
        repo=repo,
        dry_run=dry_run,
        config=cfg,
        command=command,
        config_source=config_source,
        only=tuple(only),
    )
    try:
        report.base_sha, report.base_unmet, report.base_observable = base_gate(client, repo, cfg)
    except GhError as exc:
        report.base_observable = False
        report.outcomes.append(Outcome(0, "(base branch)", "QUERY-FAILED", f"base gate: {exc}"))

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

        # A query failure on ONE PR must not abort the sweep: posting is a side
        # effect, and aborting would lose the report of what was already posted.
        try:
            checks = client.list_check_runs(repo, head_sha) if head_sha else []
        except GhError as exc:
            report.outcomes.append(Outcome(number, title, "QUERY-FAILED", f"check-runs: {exc}"))
            continue

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
        if report.base_observable and report.base_unmet and not allow_red_base:
            report.outcomes.append(
                Outcome(
                    number,
                    title,
                    "SKIP-BASE-RED",
                    "PR-side conditions met; base gate unmet: " + "; ".join(report.base_unmet),
                )
            )
            continue

        # (5) idempotency — a live command for THIS head already exists.
        try:
            comments = client.list_comments(repo, number)
            head_ts = client.head_committed_at(repo, head_sha)
        except GhError as exc:
            report.outcomes.append(Outcome(number, title, "QUERY-FAILED", f"comments/head: {exc}"))
            continue
        if has_live_command(comments, head_ts, command) and not (
            repost_waiting and state == "waiting"
        ):
            report.outcomes.append(
                Outcome(
                    number,
                    title,
                    "SKIP-LIVE-COMMAND",
                    f"{command!r} already posted for this head"
                    + (" (its attempt answered neutral)" if state == "waiting" else ""),
                )
            )
            continue

        if dry_run:
            report.outcomes.append(
                Outcome(
                    number,
                    title,
                    "WOULD-POST",
                    f"all {len(cfg.required_checks)} required checks success -> {command!r}",
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

        # (3) verify the ARTIFACT — a NEW in_progress/success Mergify check-run.
        def _describe(ch: list[dict[str, Any]], _pr: dict[str, Any] = pr) -> str:
            return _describe_unmet(_pr, ch, cfg, report.base_unmet)

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
    tool exists to avoid. `before_id` is the newest Mergify run at the moment of
    the post; only a run strictly newer than it counts.
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
        "--repost-waiting",
        action="store_true",
        help="re-request entry for a PR whose prior command already resolved to completed/neutral",
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
        config_text, config_source = load_config_text(args.config)
        cfg = parse_config(config_text)
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
            config_source=config_source,
            spacing=args.spacing,
            post_cap=args.post_cap,
            poll_timeout=args.poll_timeout,
            poll_interval=args.poll_interval,
            allow_red_base=args.allow_red_base,
            repost_waiting=args.repost_waiting,
        )
    except GhError as exc:
        print(f"QUERY FAILED: {exc}", file=sys.stderr)
        return 2

    print(report.render())
    counts = report.tally()
    if any(counts.get(v) for v in ("POST-FAILED", "UNKNOWN", "QUERY-FAILED", "SKIP-CAP")):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
