#!/usr/bin/env python3
"""The one authoritative per-commit CI verdict (#5042).

Every consumer that answers "is this commit green?" today re-derives one from a
different reading — a per-name rollup, the legacy ``/commits/<sha>/status``
endpoint, a marker-text vocabulary, a rate/envelope heuristic. This module is the
single reading they are meant to share.

Contract
--------
Given a commit sha (full 40-hex), return ONE verdict whose state is one of::

    green | red | in-flight | no-verdict

computed from the check-runs attached to **that sha**, grouped by
``(app.slug, workflow identity, job name)`` and reduced to the **latest attempt**
in each group. The workflow identity is resolved from the check-run's
``details_url`` (``.../actions/runs/<run_id>/job/<job_id>`` -> ``run_id`` -> the
run's ``workflow_id`` via
``GET /repos/{owner}/{repo}/actions/runs?head_sha=<sha>``). The **workflow id**,
not the display name, is the identity — two workflow files may share a ``name:``
and a name-keyed group would let one mask the other. When the identity cannot be
resolved:

* a run id WAS resolved from ``details_url`` but the run is absent from the
  listing (or the listing entry carries no ``workflow_id``) -> the **run id
  itself** is the identity (``run:<id>``), so two runs can never collapse into
  one group. This is deliberately fail-closed: it can keep two runs of one
  workflow apart (a possible false red), but it can never let a newer green mask
  an older red (a false green) — and a false green is the failure the whole
  refactor exists to remove.
* NO run id was resolved **and** the check is provably not an Actions check (a
  real vendor app whose URL carries no ``/actions/runs/`` path) -> the workflow
  dimension is empty and the group is keyed by its app and job name.
* NO run id was resolved but the check **is** Actions (its app is
  ``github-actions``, or its ``details_url`` carries ``/actions/runs/``) -> the
  check is grouped **per entry**. With no stable identity it must not share the
  ``(app, None, job)`` fallback group: two workflows — or two runs — sharing a
  job name would collapse there and a newer green would mask an older red, the
  exact fail-open this module exists to close.

Attempt order is the check-run **``id``** — a re-run adds a new check-run and
never replaces the old one, so ordering by ``started_at`` alone loses the
attempt (and ``started_at`` is nullable). ``started_at`` is a tiebreak only. A
check-run that is unnamed, named with the placeholder, id-less, or a duplicate
id is grouped **per entry**, so it can never collapse with — and be superseded by
— another attempt.

Absence is its own value, never a negative
------------------------------------------
A check that was never scheduled, was cancelled while queued, or has not
completed produced **no verdict**. It must never be coercible into ``red``:

* a check whose status is one of the NAMED in-flight spellings
  (``queued``/``in_progress``/``waiting``/``requested``/``pending``) is
  ``in-flight``; so is a check whose status this module has not seen **and whose
  conclusion carries no signal**;
* a completed check concluded ``cancelled`` or ``stale`` measured nothing and is
  **absent** — neither green nor red (#4757, #4831).

⛔ **A red is not cleared by absence or by a non-measuring re-run.** A group in
which an earlier attempt was RED carries a **voided red** — and the commit reads
RED — unless a *newer* attempt re-measured it with a **success**. A cancellation
(absence) cannot clear it, and neither can a `skipped`/`neutral` re-run: those
concluded without exercising code. (Without this rule a cancelled — or skipped —
re-run plus any green group elsewhere would read GREEN, a real fail-open.)

Polarity on completed checks is fail-closed (``AGENTS.md`` check-run polarity):
``success``/``neutral``/``skipped`` are green; everything else *including a
conclusion GitHub has not documented yet and a null conclusion* is red. For an
**unrecognised** non-completed status this module does not guess "absence": it
classifies by the conclusion (a present negative conclusion is RED), because a
spelling this module has not seen is not evidence that nothing ran. Only a named
in-flight status, or an unrecognised status with no conclusion, is absence.

Aggregation
-----------
1. RED if any group is red, or carries a voided red.
2. otherwise IN-FLIGHT if any group is in-flight.
3. otherwise GREEN **only if** at least one group is green and none is voided.
4. otherwise (empty surface, or every group absent) ``no-verdict``.

A group that is absent with no earlier red does not block green, and a surface
whose every group is absent reads ``no-verdict``, never green.

Read failure is a process outcome, not a verdict: the CLI exits non-zero and
prints no verdict when the check surface cannot be read (including a truncated
or empty paginated response, and a non-40-hex sha — a short sha silently yields
a zero-run ``head_sha`` listing). The four states stay the contract; the CLI
exit status is ``0`` green, ``1`` red, ``2`` unreadable, ``3`` in-flight, ``4``
no-verdict, so a shell consumer cannot mistake a red for a green by exit status.

Coverage: ``Verdict.groups`` enumerates every group and ``Verdict.coverage`` the
groups with a non-absent latest attempt. "Non-absent" is not "measured" — a
``skipped``/``neutral`` check concluded without exercising code — so the
measured/surfaces declaration is the plan's Task 3, not a claim made here.

Stdlib only (matching ``tools/ci_timing.py``); the network layer shells out to
the authed ``gh`` CLI.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

# --- verdict states (the public contract) ---------------------------------

GREEN = "green"
RED = "red"
IN_FLIGHT = "in-flight"
NO_VERDICT = "no-verdict"

# CLI exit statuses — a shell consumer must not merge on exit 0 alone.
EXIT_GREEN = 0
EXIT_RED = 1
EXIT_UNREADABLE = 2
EXIT_IN_FLIGHT = 3
EXIT_NO_VERDICT = 4

EXIT_FOR = {GREEN: EXIT_GREEN, RED: EXIT_RED, IN_FLIGHT: EXIT_IN_FLIGHT, NO_VERDICT: EXIT_NO_VERDICT}

# --- per-group states ------------------------------------------------------

GROUP_GREEN = "green"
GROUP_RED = "red"
GROUP_IN_FLIGHT = "in-flight"
GROUP_ABSENT = "absent"

#: A completed check whose conclusion MEASURED something and did not fail.
GREEN_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

#: The completed conclusions that CLEAR an earlier red: only a check that
#: actually ran and passed re-measures the group. ``skipped``/``neutral``
#: concluded without exercising code, so a newer one of those does NOT clear a
#: measured red (it is a *voided* red, exactly like a cancellation).
CLEARING_CONCLUSIONS = frozenset({"success"})

#: A completed check whose conclusion is the ABSENCE of a measurement: a
#: cancellation or a stale run exercised nothing to fail. Never red (#4757,
#: #4831) and never green (the cancelled-only case reads ``no-verdict``).
ABSENT_CONCLUSIONS = frozenset({"cancelled", "stale"})

#: The NAMED in-flight status spellings. A non-completed status outside this set
#: is not assumed to be absence — it is classified by its conclusion (below).
IN_FLIGHT_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})

#: A present NEGATIVE conclusion. Used only for an unrecognised non-completed
#: status, where the conclusion is the only evidence about what ran.
NEGATIVE_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

#: Placeholder job name for an unnamed check-run. An unnamed check is never
#: dropped: a red this module cannot name is still a red.
UNNAMED_JOB = "(unnamed check)"

#: ``.../actions/runs/<run_id>/job/<job_id>`` (or without the ``/job`` part,
#: or with a ``?query``). A run id not followed by ``/``, ``?``, ``#`` or the
#: end of the URL is NOT resolved (fail-closed: an unknown identity is grouped
#: per entry, never collapsed by name).
RUN_ID_RE = re.compile(r"/actions/runs/(\d+)(?=[/?#]|$)")

#: The app slug GitHub Actions uses, and the path segment every Actions
#: check-run carries. Together they PROVE a check is an Actions check even when
#: its run id could not be parsed.
ACTIONS_APP = "github-actions"
ACTIONS_RUN_PATH = "/actions/runs/"

#: A ref we can bind to a verdict. A short sha is NOT acceptable: the
#: ``actions/runs?head_sha=`` filter matches only the full sha, so a prefix
#: silently yields zero runs and fabricates reds.
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

CHECK_RUNS_URL = "repos/{repo}/commits/{sha}/check-runs?filter=all&per_page=100"
RUNS_URL = "repos/{repo}/actions/runs?head_sha={sha}&per_page=100"

CHECK_RUNS_KEY = "check_runs"
RUNS_KEY = "workflow_runs"


class SurfaceReadError(RuntimeError):
    """The check surface could not be read. Never a verdict, never green."""


# --- pure core -------------------------------------------------------------


@dataclass(frozen=True)
class GroupVerdict:
    """The latest attempt of one ``(app.slug, workflow, job)`` group."""

    app: str
    workflow: str | None
    workflow_key: str | None
    job: str
    state: str
    check_id: int | None
    status: str
    conclusion: str | None
    details_url: str
    started_at: str | None
    voided_red: bool = False


@dataclass(frozen=True)
class Verdict:
    """One commit's verdict, bound to its sha (and repo), with its coverage."""

    sha: str
    verdict: str
    groups: tuple[GroupVerdict, ...] = field(default_factory=tuple)
    repo: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        counts = {GROUP_GREEN: 0, GROUP_RED: 0, GROUP_IN_FLIGHT: 0, GROUP_ABSENT: 0}
        for group in self.groups:
            # A voided red is a RED for every consumer. Counting it as `absent`
            # would let a consumer that reads `counts.red` (instead of the
            # verdict or the exit code) read a false green.
            state = GROUP_RED if group.voided_red else group.state
            counts[state] = counts.get(state, 0) + 1
        return counts

    @property
    def coverage(self) -> tuple[GroupVerdict, ...]:
        """Groups whose latest attempt is not absent — i.e. the commit was read.

        NOTE: "non-absent" is not "measured". A ``skipped``/``neutral`` check is
        green here yet concluded without exercising code (the rail's
        ``MEASURING_CONC`` excludes both). The measured/surfaces declaration is
        plan Task 3; this property is only the enumeration of what was read.
        """
        return tuple(group for group in self.groups if group.state != GROUP_ABSENT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "repo": self.repo,
            "verdict": self.verdict,
            "counts": self.counts,
            "groups": [asdict(group) for group in self.groups],
        }


def resolve_workflow_run_id(details_url: str | None) -> str | None:
    """Resolve a check-run's Actions run id from its ``details_url``.

    Returns ``None`` for a non-Actions check (the URL has no ``/actions/runs/``
    path) or an unparseable one.
    """
    if not details_url:
        return None
    match = RUN_ID_RE.search(str(details_url))
    return match.group(1) if match else None


def build_run_workflow_map(
    runs: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[str | None, str | None]]:
    """Map an Actions ``run_id`` -> ``(workflow_key, workflow_name)``.

    ``workflow_key`` is the run's ``workflow_id`` ONLY — never the display name.
    A run without a ``workflow_id`` maps to ``(None, name)`` and its check-runs
    fall back to the ``run:<id>`` identity, so two same-named workflow files
    cannot collapse into one group.
    """
    out: dict[str, tuple[str | None, str | None]] = {}
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        run_id = run.get("id")
        if run_id is None:
            continue
        workflow_id = run.get("workflow_id")
        name = run.get("name")
        key = str(workflow_id) if workflow_id is not None else None
        out[str(run_id)] = (key, str(name) if name else None)
    return out


def group_state(status: str | None, conclusion: str | None) -> str:
    """Classify one check-run's attempt. See the module docstring for the rules."""
    if status == "completed":
        if conclusion in GREEN_CONCLUSIONS:
            return GROUP_GREEN
        if conclusion in ABSENT_CONCLUSIONS:
            return GROUP_ABSENT
        # Fail-closed polarity on completed checks, including unknown and null.
        return GROUP_RED
    if status in IN_FLIGHT_STATUSES:
        return GROUP_IN_FLIGHT
    # An unrecognised non-completed spelling: not evidence that nothing ran.
    if conclusion in NEGATIVE_CONCLUSIONS:
        return GROUP_RED
    return GROUP_IN_FLIGHT


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attempt_order(check_id: int | None, started_at: str | None) -> tuple[int, str]:
    """Order attempts: the check-run id decides; ``started_at`` breaks ties only.

    ``started_at`` is nullable, which is exactly why it cannot be the primary
    key — ordering by it would lose the attempt.
    """
    return (check_id if check_id is not None else -1, started_at or "")


def _workflow_identity(
    run_id: str | None,
    resolved: tuple[str | None, str | None] | None,
) -> tuple[str | None, str | None]:
    """Return ``(workflow_key, workflow_name)`` for the group key."""
    if not run_id:
        return None, None
    if resolved and resolved[0]:
        return f"wf:{resolved[0]}", resolved[1]
    # The run id resolved but its workflow identity did not: keep the run as the
    # identity so two runs never collapse (fail-closed against a masked red).
    return f"run:{run_id}", None


def _is_provably_non_actions(app: str, details_url: str) -> bool:
    """True only when a check is PROVABLY not an Actions check.

    The workflow-identity fallback key is ``(app, None, job)``. Sharing it is
    safe only for a genuine non-Actions check, where the app IS the identity.
    An Actions check whose run id failed to resolve has no stable identity, so
    it must be grouped per entry instead of sharing that fallback group —
    otherwise two same-named workflow files (or two runs) collapse and one can
    mask the other.
    """
    return app not in ("unknown", ACTIONS_APP) and ACTIONS_RUN_PATH not in details_url


def group_latest_attempts(
    check_runs: Iterable[Mapping[str, Any]],
    run_workflow_map: Mapping[str, tuple[str | None, str | None]] | None = None,
) -> tuple[GroupVerdict, ...]:
    """Reduce every check-run on a sha to the latest attempt per group.

    Group key: ``(app.slug, workflow identity, job name)``. A check-run that is
    unnamed, named with the placeholder, id-less, or part of a duplicate-id set
    gets a per-entry key (unique per occurrence), so a newer non-red can never
    supersede (and mask) an older red before classification. A group that had an
    earlier MEASURED red which no newer success re-measured carries
    ``voided_red`` (a cancellation or a ``skipped``/``neutral`` re-run cannot
    clear it). The returned tuple is deterministically ordered.
    """
    run_workflow_map = run_workflow_map or {}
    best: dict[tuple[Any, ...], GroupVerdict] = {}
    base_of: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    seen_ids: set[tuple[Any, ...]] = set()
    # Per base group, the newest order of a MEASURED red and of a CLEARING
    # (success) attempt. A red survives unless a newer success re-measured it.
    newest_red: dict[tuple[Any, ...], tuple[int, str]] = {}
    newest_clear: dict[tuple[Any, ...], tuple[int, str]] = {}
    for index, check_run in enumerate(check_runs):
        if not isinstance(check_run, Mapping):
            continue
        raw_name = str(check_run.get("name") or "").strip()
        app_obj = check_run.get("app")
        app = (
            str((app_obj or {}).get("slug") or "unknown")
            if isinstance(app_obj, Mapping)
            else "unknown"
        )
        check_id = _as_int(check_run.get("id"))
        status = str(check_run.get("status") or "")
        conclusion = check_run.get("conclusion")
        conclusion = str(conclusion) if conclusion is not None else None
        details_url = str(check_run.get("details_url") or "")
        run_id = resolve_workflow_run_id(details_url)
        workflow_key, workflow_name = _workflow_identity(
            run_id, run_workflow_map.get(run_id) if run_id else None
        )
        started_at = check_run.get("started_at")
        started_at = str(started_at) if started_at is not None else None

        job = raw_name or UNNAMED_JOB
        state = group_state(status, conclusion)
        base_key: tuple[Any, ...] = (app, workflow_key, job)
        # A per-entry group is used whenever the check has no stable identity:
        # unnamed, placeholder-named, id-less, an id already seen in its group,
        # or a check whose workflow identity did not resolve and which is not
        # provably a non-Actions check. The last case is load-bearing: an
        # Actions check with an unparseable/absent run id would otherwise share
        # the (app, None, job) fallback group, letting two workflows that share
        # a job name collapse and a newer green mask an older red.
        per_entry = (
            not raw_name
            or job == UNNAMED_JOB
            or check_id is None
            or (base_key, check_id) in seen_ids
            or (workflow_key is None and not _is_provably_non_actions(app, details_url))
        )
        key: tuple[Any, ...] = (*base_key, "entry", index) if per_entry else base_key
        if check_id is not None:
            seen_ids.add((base_key, check_id))
        base_of[key] = base_key
        order = _attempt_order(check_id, started_at)
        if state == GROUP_RED and (base_key not in newest_red or order > newest_red[base_key]):
            newest_red[base_key] = order
        elif (
            state == GROUP_GREEN
            and conclusion in CLEARING_CONCLUSIONS
            and (base_key not in newest_clear or order > newest_clear[base_key])
        ):
            newest_clear[base_key] = order

        entry = GroupVerdict(
            app=app,
            workflow=workflow_name,
            workflow_key=workflow_key,
            job=job,
            state=state,
            check_id=check_id,
            status=status,
            conclusion=conclusion,
            details_url=details_url,
            started_at=started_at,
        )
        current = best.get(key)
        if current is None or _attempt_order(entry.check_id, entry.started_at) > _attempt_order(
            current.check_id, current.started_at
        ):
            best[key] = entry

    # A red is not cleared by absence (or by a non-measuring re-run): mark a
    # group whose newest MEASURED red is not superseded by a newer success.
    groups = []
    for key, entry in best.items():
        base = base_of.get(key)
        if (
            entry.state != GROUP_RED
            and base in newest_red
            and (base not in newest_clear or newest_red[base] > newest_clear[base])
        ):
            entry = replace(entry, voided_red=True)
        groups.append(entry)
    return tuple(
        sorted(
            groups,
            key=lambda group: (
                group.app,
                group.workflow_key or "",
                group.job,
                group.check_id if group.check_id is not None else -1,
                group.details_url,
            ),
        )
    )


def aggregate(groups: Sequence[GroupVerdict]) -> str:
    """Fold per-group states into the one commit verdict (see module docstring)."""
    if not groups:
        return NO_VERDICT
    states = {group.state for group in groups}
    if GROUP_RED in states or any(group.voided_red for group in groups):
        return RED
    if GROUP_IN_FLIGHT in states:
        return IN_FLIGHT
    if GROUP_GREEN in states:
        return GREEN
    return NO_VERDICT


def compute_verdict(
    sha: str,
    check_runs: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]] = (),
    repo: str | None = None,
) -> Verdict:
    """Compute the verdict for ``sha`` from already-fetched payloads (pure).

    The full-40-hex sha invariant is enforced HERE, not only in the fetch
    layer: the offline CLI seam (``--check-runs-json``/``--runs-json``) and the
    merge rail that will feed its already-fetched payload through it call this
    function directly, and a short sha silently yields a zero-run ``head_sha``
    listing.
    """
    _require_full_sha(sha)
    run_workflow_map = build_run_workflow_map(runs)
    groups = group_latest_attempts(check_runs, run_workflow_map)
    return Verdict(sha=sha, repo=repo, verdict=aggregate(groups), groups=groups)


# --- network layer ---------------------------------------------------------


def _decode_pages(raw: str) -> list[Any]:
    """Decode ``gh api --paginate`` output: one JSON value per page, concatenated."""
    decoder = json.JSONDecoder()
    out: list[Any] = []
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        obj, index = decoder.raw_decode(raw, index)
        out.append(obj)
    return out


def _gh_api(url: str) -> list[Any]:
    try:
        proc = subprocess.run(
            ["gh", "api", url, "--paginate"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise SurfaceReadError(f"gh CLI not found: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise SurfaceReadError(
            f"gh api {url} failed (rc={proc.returncode})"
            + (f": {detail[-1][:400]}" if detail else "")
        )
    if not proc.stdout.strip():
        # An empty body is a READ FAILURE, not an empty surface: treating it as
        # "no checks" would let an unreadable surface certify green.
        raise SurfaceReadError(f"gh api {url} returned an empty body")
    try:
        return _decode_pages(proc.stdout)
    except ValueError as exc:
        raise SurfaceReadError(f"gh api {url} returned unparseable JSON: {exc}") from exc


def collect_items(
    pages: Iterable[Any],
    items_key: str,
    url: str,
) -> list[Mapping[str, Any]]:
    """Flatten paginated ``items_key`` lists and fail closed on truncation.

    A paginated read is complete only when the response declares an integer
    ``total_count`` and the number of items collected matches it. A missing
    declaration or a short list is a READ FAILURE — an unreadable shard list is
    not an empty one, and a list whose completeness cannot be witnessed is not a
    surface.
    """
    out: list[Mapping[str, Any]] = []
    totals: list[int] = []
    count = 0
    for page in pages:
        count += 1
        if not isinstance(page, Mapping):
            raise SurfaceReadError(f"gh api {url} returned a non-object page")
        items = page.get(items_key)
        if isinstance(items, list):
            out.extend(item for item in items if isinstance(item, Mapping))
        total = page.get("total_count")
        if isinstance(total, int):
            totals.append(total)
    if count == 0:
        raise SurfaceReadError(f"gh api {url} returned no pages")
    if not totals:
        raise SurfaceReadError(
            f"gh api {url} carried no integer total_count — completeness cannot be witnessed"
        )
    expected = max(totals)
    if len(out) < expected:
        raise SurfaceReadError(
            f"gh api {url}: read {len(out)} of {expected} {items_key} — the listing is TRUNCATED"
        )
    return out


def _require_full_sha(sha: str) -> str:
    if not FULL_SHA_RE.match(sha or ""):
        raise SurfaceReadError(
            f"{sha!r} is not a full 40-hex sha — a short ref silently yields a zero-run "
            "'actions/runs?head_sha=' listing and fabricates reds"
        )
    return sha


def fetch_check_runs(repo: str, sha: str) -> list[Mapping[str, Any]]:
    """Every check-run on ``sha`` (every attempt — ``filter=all``)."""
    url = CHECK_RUNS_URL.format(repo=repo, sha=_require_full_sha(sha))
    return collect_items(_gh_api(url), CHECK_RUNS_KEY, url)


def fetch_runs(repo: str, sha: str) -> list[Mapping[str, Any]]:
    """The Actions runs whose ``head_sha`` is ``sha`` (for workflow resolution)."""
    url = RUNS_URL.format(repo=repo, sha=_require_full_sha(sha))
    return collect_items(_gh_api(url), RUNS_KEY, url)


def read_verdict(repo: str, sha: str) -> Verdict:
    """Fetch and compute the verdict for ``sha``. Raises ``SurfaceReadError``."""
    sha = _require_full_sha(sha)
    # Read the check surface first: an unreadable check surface is fatal on its
    # own, and the runs listing is only a workflow-identity lookup.
    check_runs = fetch_check_runs(repo, sha)
    runs = fetch_runs(repo, sha)
    return compute_verdict(sha, check_runs, runs, repo=repo)


def _load_pages_file(path: str) -> list[Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SurfaceReadError(f"cannot read {path}: {exc}") from exc
    if not raw.strip():
        raise SurfaceReadError(f"{path} is empty — that is a read failure, not an empty surface")
    try:
        return _decode_pages(raw)
    except ValueError as exc:
        raise SurfaceReadError(f"{path} is not valid (concatenated) JSON: {exc}") from exc


# --- CLI -------------------------------------------------------------------


def _render_text(verdict: Verdict) -> str:
    counts = verdict.counts
    lines = [
        f"verdict={verdict.verdict}",
        f"sha={verdict.sha}",
        "counts"
        f" groups={len(verdict.groups)}"
        f" green={counts[GROUP_GREEN]}"
        f" red={counts[GROUP_RED]}"
        f" in_flight={counts[GROUP_IN_FLIGHT]}"
        f" absent={counts[GROUP_ABSENT]}",
    ]
    for group in verdict.groups:
        wf = group.workflow if group.workflow else "-"
        voided = " voided-red" if group.voided_red else ""
        lines.append(
            f"group\t{group.state}{voided}\t{group.app}\t{wf}\t{group.job}\t"
            f"{group.conclusion or '-'}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_verdict.py",
        description=(
            "One authoritative per-commit CI verdict (#5042). "
            "Exit: 0 green, 1 red, 2 unreadable, 3 in-flight, 4 no-verdict."
        ),
    )
    parser.add_argument("sha", help="the commit sha to resolve (full 40-hex)")
    parser.add_argument(
        "--repo",
        default=None,
        help="owner/name (default: gh's own resolution for the current checkout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full verdict object as JSON",
    )
    parser.add_argument(
        "--check-runs-json",
        default=None,
        metavar="FILE",
        help="offline: read check-runs from FILE (gh api --paginate output) instead of fetching",
    )
    parser.add_argument(
        "--runs-json",
        default=None,
        metavar="FILE",
        help="offline: read actions/runs from FILE so workflows can be resolved",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check_runs_json:
            url = args.check_runs_json
            check_runs = collect_items(_load_pages_file(url), CHECK_RUNS_KEY, url)
            runs: list[Mapping[str, Any]] = []
            if args.runs_json:
                runs = collect_items(_load_pages_file(args.runs_json), RUNS_KEY, args.runs_json)
            if not runs and any(resolve_workflow_run_id(cr.get("details_url")) for cr in check_runs):
                print(
                    "ci_verdict: WARNING — Actions check-runs are present but no runs listing was "
                    "supplied; workflow identity degrades to the run id (a possible false red)",
                    file=sys.stderr,
                )
            verdict = compute_verdict(args.sha, check_runs, runs, repo=args.repo)
        else:
            # `gh api` substitutes the {owner}/{repo} placeholders for the
            # current checkout when --repo is not given.
            verdict = read_verdict(args.repo or "{owner}/{repo}", args.sha)
    except SurfaceReadError as exc:
        print(f"ci_verdict: could not read the check surface: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, sort_keys=True))
    else:
        print(_render_text(verdict))
    return EXIT_FOR[verdict.verdict]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
