#!/usr/bin/env python3
"""Merge-throughput instrument (#5215) — owns the pass/fail exit-code contract.

Every success criterion in the merge-throughput plan is the exit code of this
tool, never a shell pipeline. A `jq` comparison over a sentinel is how a
criterion passes for exactly the reason it exists to fail:

    "UNKNOWN" >= 12   # true in jq — strings order above numbers
    null <= 120       # true
    {} | .conflicts.total != "UNKNOWN"   # true (a missing key is null)
    jq '.gap'         # exits 0 always

So the contract is here, and it is:

    check <name> ...  →  0 satisfied · 1 threshold miss · 2 UNKNOWN/absent

Empty, unavailable, partial, or non-JSON is UNKNOWN (2), never 0. A 0-byte or
HTML-error body is UNKNOWN. Polarity is an ALLOW-LIST: only
success/neutral/skipped are green; cancelled/stale are non-red; **anything
else, including a null conclusion, is RED**. Check-runs are grouped by
(app, workflow, job name) with the newest attempt per group BY ID — grouping by
job name alone collapses two workflows' same-named jobs into a false GREEN.

Reads: `gh api` only (never `/commits/<sha>/status`; never `mergeStateStatus`).
Conflicts: `git merge-tree`, because the GitHub mergeability field under-reports
(36 vs the true 44).

Spec: the #5215 merge-throughput plan §10 Task 1.

Stdlib only; must import on the `python3` the §11 criteria use, so nothing
newer than 3.9 (see the `timezone.utc` note below) and `datetime.UTC` is
avoided deliberately.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import json as jsonlib
import math
import os
import re
import statistics
import subprocess
import sys
import urllib.parse

# `timezone.utc`, not `datetime.UTC` (3.11+): every §11 criterion invokes this
# tool as `python3 tools/merge_throughput.py`, and a crash at import exits 1 —
# the contract's MISS — so a newer-interpreter-only import fabricates a red.
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OWNER_REPO = "daniel-ospina/tortoise"

UNKNOWN = "UNKNOWN"

# THE polarity token set. Mirrors scripts/admin-merge.sh:1762 NON_RED_CONC —
# pinned in one committed list here so the rail's rule has a parity test
# (test_non_red_token_set_matches_the_rail). The RAIL IS AUTHORITATIVE; these
# tokens are the instrument's copy of the rule.
NON_RED_CONCLUSIONS = frozenset({"success", "neutral", "skipped", "cancelled", "stale"})
GREEN_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
STRICT_GREEN = frozenset({"success"})
IN_FLIGHT_STATUSES = frozenset(
    {"queued", "in_progress", "waiting", "requested", "pending"}
)
#: The rail's MEASURING predicate (admin-merge.sh MEASURING_CONC). Only a
#: COMPLETED check run with one of these conclusions actually EXERCISED
#: something, so only these may set the evaluated surface's last-production
#: time that §4.6's staleness comparison reads. A `skipped`/`cancelled`/
#: `neutral`/`stale` run measured NOTHING and must not advance the anchor —
#: letting one advance it moved the anchor forward past the real evaluation and
#: made an uncovered base red compare as already-measured (#1353).
MEASURING_CONCLUSIONS = frozenset(
    {"success", "failure", "timed_out", "action_required"}
)
#: The placeholder identity for an unnamed check run. The rail CLASSIFIES an
#: unnamed run rather than dropping it (a dropped red could vanish from the
#: surface), and this keeps the same property here.
UNNAMED_CHECK = "(unnamed check)"
#: The evaluated-tree verdict tokens — the rail's three refusal classes.
SURFACE_GREEN, SURFACE_RED, SURFACE_PENDING = "GREEN", "RED", "PENDING"
_SURFACE_VERDICTS = (SURFACE_GREEN, SURFACE_RED, SURFACE_PENDING)

# Structural population floor (#5215 cycle 9): a self-consistent 1-item read
# must fail, so the check derives its own total_count and requires it to clear
# this committed sanity floor. `--min-population` may only RAISE it.
MIN_OPEN_PR_POPULATION = 10

# drain-rate floors (#5215 cycle 5): the window must be long enough and the
# observed merge count large enough that a rate is a measurement, not noise.
DRAIN_WINDOW_MIN_HOURS = 24
DRAIN_MIN_MERGES = 5

DEFAULT_SWEEP_CONCURRENCY = 4
HARD_MAX_SWEEP_CONCURRENCY = 8

DEFAULT_RECORD_WINDOW_DAYS = 7
M4_RECORD_WINDOW_DAYS = 14
GH_API_ATTEMPTS = 3
# S11 cycle 8: `--require-fresh` validates each of these fields' OWN record —
# one fresh M4 record cannot vouch for a stale capacity_at_first_failure.
# `oldest_minutes` is included because it is the value S11 thresholds.
_CAPACITY_FRESH_FIELDS = (
    "queued", "in_progress", "oldest_minutes", "capacity_at_first_failure",
)
I1_RECORD_WINDOW_DAYS = 90

GAP_TERM_MIN = 6
#: The six named terms of the gap decomposition (S13). The count floor alone
#: would let arbitrary sourced keys stand in for a missing named term.
_GAP_TERMS = frozenset({
    "effective_parallel", "effective_batch", "cycle_minutes", "wait",
    "ceiling", "observed",
})
#: Checks that implement a real freshness test for `--require-fresh`.
_FRESHNESS_CHECKS = frozenset({
    "main-gate", "queue-entry", "batch-size", "cycle", "attribution",
    "capacity", "parallelism-headroom", "gap", "no-languish", "drain-rate",
    "prs-per-day",
})
# S13: `.gap.value` is independently measured as ceiling / observed, so the
# reconciliation can fail. Tolerance stated numerically (±10%).
GAP_VALUE_TOLERANCE = 0.10
CEILING_MAX = 200000
CEILING_TOLERANCE = 0.10  # ±10%, stated numerically

EXCLUDE_KEYS = frozenset({"hard_stop", "terminal_decision", "draft", "superseded_by"})

# ---------------------------------------------------------------------------
# Task 5 — eligibility triage (plan §10 Task 5).
#
# The bucket taxonomy is Task 5's. `bucket` is FIRST-MATCH-WINS because the
# categories overlap BY CONSTRUCTION (a recovered `wip` snapshot can be a
# guard-surface PR AND a draft AND conflicted AND superseded all at once).
# Task 1 emitted UNKNOWN for every Task-5-owned judgement so the two tasks
# could not silently disagree; these are the judgements it deferred.
# ---------------------------------------------------------------------------

BUCKET_ORDER = (
    "hard_stop",          # E7 / D12 — a wrong resolution silently disables a check
    "terminal_decision",  # E5 / D5 — asserts the OPPOSITE contract to main
    "dead_weight",        # E3 — the work already landed; nothing to merge
    "draft",              # E4 — contract-bound verbatim preservation
    "conflicting",        # git merge-tree conflict
    "blocked",            # rail class (b): the PR's OWN surface is red/pending
    "re_measure",         # rail class (c): a stale GREEN surface (§4.6/§4.7)
    "eligible",           # rail class (a) ACCEPT: green, fresh, conflict-free
)

#: The EXACT row schema. `--exclude` may name only keys from this set.
#:
#: `surface_evidence` is the post-spec addition the rail's THREE refusal classes
#: (ACCEPT / BLOCK / RE-MEASURE) require. The plan's six buckets were an
#: ownership taxonomy in which `eligible` never asserted the rail would take
#: the PR; the orchestrator's third class does, so bucketing a stale GREEN
#: surface cannot be done without also deciding whether the surface is green —
#: and that decision, with its timestamp evidence, must travel ON the row.
#: `blocked` (class b, "the correct refusal, not itself a bug") is the other
#: half: without it a red-surface PR would fall through to `eligible`, which no
#: longer means what the worklist says it means.
TRIAGE_SCHEMA_KEYS = (
    "number", "bucket", "eligible", "conflict", "conflicted_paths",
    "superseded_by", "surface_evidence", "draft", "hard_stop",
    "terminal_decision", "owner", "owner_evidence", "owning_issue",
)

#: E7 — HARD-STOP surfaces. This is D12's DECISION QUEUE (owner Daniel, by
#: 2026-10-03): a wrong conflict resolution silently disables a check, so no
#: lane may resolve one. Membership is the plan's named set; the guard surface
#: each PR touches is recorded in `owner_evidence`/the worklist narrative.
HARD_STOP_D12 = frozenset({5136, 5461, 5465, 5467, 5468})

#: E5 — terminal conflicts asserting the OPPOSITE contract to main. D5: Daniel,
#: 2026-10-03, per-PR adjudication (never a silent rebase). **#5196 is NOT here:
#: its own body places the absent-raw state on the existing `:Source` record
#: (`not a fourth kind of source`, STORAGE §9.4 ③), which is what main's D10
#: doctrine already says — the plan's E5 claim for it does not reproduce, and a
#: union rebase is mechanical. It is `conflicting`.**
TERMINAL_D5 = frozenset({5285, 4963})

#: E3 — dead weight, each with POSITIVE evidence that its work is already on
#: main. `superseded_by` is evidence, never an inference; a candidate with no
#: such evidence must NOT be placed here (it stays `draft`/`conflicting`).
DEAD_WEIGHT_EVIDENCE = {
    5190: "#3405 (close_failed_at on main)",
    5453: "#4825 (merged PR for #4625; _update_onboarding_state on main)",
    5455: "#2984 (issue #2922) — the function-local `import os` guard fix is on main",
}

#: EVERY lane on this fleet authenticates as this one account, so a PR author
#: login can never name a lane. `owner` must never be this string.
FLEET_AUTHOR_LOGIN = "daniel-ospina"

#: The committed evidence file a live `--triage` reads for owner attribution.
#: Absent/unreadable is UNKNOWN owners — never a guess from the PR author.
TRIAGE_OWNER_EVIDENCE_PATH = REPO / "docs" / "ci" / "triage" / "owner-evidence.json"

CHECK_NAMES = (
    "main-gate",
    "drain-rate",
    "prs-per-day",
    "queue-entry",
    "queue-eta",
    "batch-size",
    "cycle",
    "shard-balance",
    "fast-files-unclassified",
    "conflicts",
    "attribution",
    "capacity",
    "parallelism-headroom",
    "gap",
    "no-languish",
    "baseline-fresh",
    "assert-queue-head-checks",
    "durations-map",
)

USAGE = """\
merge_throughput.py check <name> [--min N] [--max N] [--pr N] [--min-depth N]
                                 [--min-population N] [--exclude a,b,c]
                                 [--or-artifact PATH#ANCHOR]
                                 [--strict] [--max-oldest-minutes M]
                                 [--min-headroom H] [--max-age-days D]
                                 [--require-complete] [--require-fresh]
                                 [--and <check-name> <that check's threshold flags> ...]
merge_throughput.py --json [<name>|<field-path>]   # emit all fields, never a judgement
merge_throughput.py --triage [--emit rows]         # never issues a mutating request
merge_throughput.py --watch-queue | --observe-capacity
merge_throughput.py --sweep-concurrency N          # bounded merge-tree sweep

Record input (all checks): --input PATH.json  (a whole-record object, or a
check-name -> record map). Fixtures are test-only and require
MERGE_THROUGHPUT_ALLOW_FIXTURE=1.
"""


# ---------------------------------------------------------------------------
# Small typed helpers.
# ---------------------------------------------------------------------------

def _is_num(value: object) -> bool:
    """A real, finite number. `bool`, NaN and Infinity are not measurements."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _as_number(value: object):
    """Coerce a CLI string / fixture value to a number, else None.

    `bool` is deliberately rejected: `True` is not the measurement `1`, and
    mapping "not a real value" to a passing number is a fail-open.
    """
    if value is None or value == "" or value is UNKNOWN or isinstance(value, bool):
        return None
    if _is_num(value):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: object):
    n = _as_number(value)
    if n is None:
        return None
    return int(n)


def _parse_ts(value: object):
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017 - must import on 3.9 (see header)
    return dt


def _age_days(value: object):
    dt = _parse_ts(value)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0  # noqa: UP017


def _fresh_record(record) -> bool:
    """A capacity/M4 field's OWN record is fresh (dict, or a path on disk)."""
    if isinstance(record, dict):
        return _age_ok(record.get("verified_at"), M4_RECORD_WINDOW_DAYS)
    if isinstance(record, str) and record:
        try:
            resolved = (REPO / record).resolve()
            resolved.relative_to(REPO.resolve())
            data = jsonlib.loads(resolved.read_text())
        except (ValueError, OSError):
            return False
        return isinstance(data, dict) and _age_ok(
            data.get("verified_at"), M4_RECORD_WINDOW_DAYS
        )
    return False


def _age_ok(verified_at: object, window_days: float) -> bool:
    """A record is fresh only if its age is in [0, window].

    A far-future `verified_at` yields a negative age, which must NOT pass a
    freshness test (the primitive `baseline-fresh` is built on).
    """
    age = _age_days(verified_at)
    return age is not None and 0 <= age <= window_days


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


# ---------------------------------------------------------------------------
# Transport invariant: a non-2xx, 0-byte, or non-JSON body is UNKNOWN.
# ---------------------------------------------------------------------------

def parse_api_body(body: object):
    """Parse an API body; UNKNOWN for 0-byte / non-JSON / JSON-null.

    The invariant is checked at every read site: a 0-byte body is not an empty
    list, and an HTML error page is not a zero value.
    """
    if body is None:
        return UNKNOWN
    if isinstance(body, (bytes, bytearray)):
        if len(body) == 0:
            return UNKNOWN
        try:
            text = bytes(body).decode("utf-8")
        except UnicodeDecodeError:
            return UNKNOWN
    elif isinstance(body, str):
        if not body.strip():
            return UNKNOWN
        text = body
    else:
        return body
    try:
        parsed = jsonlib.loads(text)
    except (TypeError, ValueError):
        return UNKNOWN
    if parsed is None:
        return UNKNOWN
    return parsed


# ---------------------------------------------------------------------------
# Check-run verdicts — allow-list polarity, grouped by (app, workflow, name).
# ---------------------------------------------------------------------------

def _app_slug(run: dict) -> str:
    app = run.get("app")
    if isinstance(app, dict) and app.get("slug"):
        return str(app["slug"])
    return "unknown"


def _run_id(run: dict):
    """The attempt's `id` as an int, else None (unorderable)."""
    value = run.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _workflow_key(run: dict, workflow_of=None):
    """Resolve the workflow identity for a check-run.

    Order: an explicit `workflow`/`workflow_name` field, then the caller's
    resolver (the live path maps run id → workflow name from the Actions API),
    then the run id parsed out of `details_url`. `None` means unresolvable —
    and an unresolvable details_url must NEVER collapse a group into GREEN.
    """
    for field in ("workflow", "workflow_name"):
        if run.get(field):
            return str(run[field])
    if workflow_of is not None:
        try:
            resolved = workflow_of(run)
        except Exception:
            resolved = None
        if resolved:
            return str(resolved)
    url = run.get("details_url")
    if not url:
        return None
    match = re.search(r"/runs/(\d+)", str(url))
    if match:
        return f"run:{match.group(1)}"
    return f"url:{url}"


def _group_runs(runs, workflow_of=None) -> dict:
    groups: dict = {}
    for run in runs:
        key = (_app_slug(run), _workflow_key(run, workflow_of), str(run.get("name")))
        groups.setdefault(key, []).append(run)
    return groups


def _is_sha(value) -> bool:
    """A real commit sha, never an UNKNOWN sentinel.

    Two identical sentinels compare equal, so a truthy-but-unknown value would
    satisfy a head-binding check that only tested truthiness and equality.
    """
    if not isinstance(value, str):
        return False
    token = value.strip()
    return bool(token) and token.upper() != UNKNOWN


def _newest(group: list[dict]):
    """Newest attempt by `id`, or None when the group cannot be totally ordered.

    A partial order is not an order: if any attempt in a multi-attempt group
    lacks an integer `id`, or two attempts share an `id`, list order would
    decide the newest and an older red could be shadowed by a newer green.
    That is UNKNOWN, never GREEN.
    """
    if len(group) == 1:
        return group[0]
    ids = [_run_id(run) for run in group]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        return None
    return max(group, key=_run_id)


def _conclusion_token(run: dict):
    if run.get("status") != "completed":
        return "in_flight"
    return run.get("conclusion")


def _token_verdict(token) -> str:
    """Lax allow-list token verdict: GREEN / NON_RED / RED.

    A null conclusion and an unrecognised token are RED; there is no
    "not in the red set" shortcut.
    """
    if token is None:
        return "RED"
    if token in GREEN_CONCLUSIONS:
        return "GREEN"
    if token in NON_RED_CONCLUSIONS:
        return "NON_RED"
    return "RED"


def _group_verdict(group: list[dict], workflow_key) -> str:
    newest = _newest(group)
    if newest is None:
        return "UNKNOWN"
    if workflow_key is None:
        # The workflow cannot be resolved. A multi-attempt group CANNOT be
        # ordered by id — a newer non-red attempt must never shadow an older
        # red — so it is UNKNOWN. A singleton still reports a RED/NON_RED
        # token, but a GREEN is unprovable and is UNKNOWN, never GREEN.
        if len(group) > 1:
            return "UNKNOWN"
        if newest.get("status") != "completed":
            return "UNKNOWN"
        verdict = _token_verdict(newest.get("conclusion"))
        return "UNKNOWN" if verdict == "GREEN" else verdict
    if newest.get("status") != "completed":
        return "UNKNOWN"
    return _token_verdict(newest.get("conclusion"))


def _verdicts(runs, workflow_of=None) -> list[tuple]:
    return [
        (key, _group_verdict(group, key[1]))
        for key, group in _group_runs(runs, workflow_of).items()
    ]


def main_gate(runs, workflow_of=None) -> str:
    """Lax allow-list polarity (diagnostic). `cancelled` is NON_RED, not green.

    GREEN only when the surface is non-empty and every group is non-red.
    """
    if not runs:
        return "UNKNOWN"
    verdicts = [v for _, v in _verdicts(runs, workflow_of)]
    if "RED" in verdicts:
        return "RED"
    if "UNKNOWN" in verdicts:
        return "UNKNOWN"
    if "NON_RED" in verdicts:
        return "NON_RED"
    return "GREEN"


def mergify_mergeable(runs, workflow_of=None) -> str:
    """STRICT polarity — `check-success`: only `success` satisfies."""
    if not runs:
        return "UNKNOWN"
    for key, group in _group_runs(runs, workflow_of).items():
        newest = _newest(group)
        if newest is None:
            return "UNKNOWN"
        if newest.get("status") != "completed":
            return "UNKNOWN"
        if newest.get("conclusion") not in STRICT_GREEN:
            return "BLOCKED"
        if key[1] is None:
            # A green whose workflow cannot be resolved is not provably this
            # group: never report it MERGEABLE.
            return "UNKNOWN"
    return "MERGEABLE"


def verdict_from_check_runs(runs, workflow_of=None) -> str:
    """Lax surface verdict. RED wins; then UNKNOWN; NON_RED is not GREEN.

    GREEN only when the surface is non-empty and EVERY group is GREEN.
    """
    if not runs:
        return "UNKNOWN"
    verdicts = [v for _, v in _verdicts(runs, workflow_of)]
    if "RED" in verdicts:
        return "RED"
    if "UNKNOWN" in verdicts:
        return "UNKNOWN"
    if "NON_RED" in verdicts:
        return "NON_RED"
    return "GREEN"


def aggregate(codes) -> int:
    """Conjunct aggregate: 2 if ANY is 2, else 1 if ANY is 1, else 0."""
    codes = list(codes)
    if any(code == 2 for code in codes):
        return 2
    if any(code == 1 for code in codes):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Bounded merge-tree sweep.
# ---------------------------------------------------------------------------

def validate_sweep_concurrency(value):
    if value is None or value == "":
        return DEFAULT_SWEEP_CONCURRENCY
    try:
        bound = int(value)
    except (TypeError, ValueError):
        print("UNKNOWN: --sweep-concurrency must be an integer", file=sys.stderr)
        raise SystemExit(2) from None
    env_cap = os.environ.get("MERGE_THROUGHPUT_MAX_CONCURRENCY")
    hard = _as_int(env_cap) if env_cap else HARD_MAX_SWEEP_CONCURRENCY
    if hard is None:
        print("UNKNOWN: MERGE_THROUGHPUT_MAX_CONCURRENCY is not an integer",
              file=sys.stderr)
        raise SystemExit(2)
    cpu = os.cpu_count() or 1
    if bound < 1 or bound > hard or bound > cpu:
        print(
            f"UNKNOWN: --sweep-concurrency {bound} exceeds the validated bound "
            f"(hard={hard}, cpu={cpu})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return bound


def bounded_map(fn, items, concurrency=DEFAULT_SWEEP_CONCURRENCY):
    """Run `fn` over `items` with at most `concurrency` in flight at once."""
    items = list(items)
    if not items:
        return []
    workers = max(1, min(int(concurrency), len(items)))
    results: list = [None] * len(items)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    return results


def merge_tree_conflict_detail(repo, main_ref: str, branch_ref: str):
    """(conflict, conflicted_paths). UNKNOWN on any unresolvable read.

    The conflicted paths are the lines between the tree OID (line 1) and the
    first blank line of `git merge-tree --write-tree --name-only` output; the
    informational `Auto-merging`/`CONFLICT` prose follows that blank line.
    """
    for ref in (main_ref, branch_ref):
        try:
            exists = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=str(repo), capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return UNKNOWN, []
        if exists.returncode != 0:
            return UNKNOWN, []
    try:
        proc = subprocess.run(
            ["git", "merge-tree", "--write-tree", "--name-only", main_ref, branch_ref],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN, []
    if proc.returncode == 0:
        return False, []
    if proc.returncode == 1:
        paths = []
        for line in proc.stdout.splitlines()[1:]:
            if line == "":
                break
            paths.append(line)
        return True, paths
    return UNKNOWN, []


def merge_tree_conflict(repo, main_ref: str, branch_ref: str):
    """git merge-tree conflict probe. True/False, or UNKNOWN.

    A deleted branch or an unresolvable ref is UNKNOWN — never "no conflict".
    """
    conflict, _paths = merge_tree_conflict_detail(repo, main_ref, branch_ref)
    return conflict


def assert_main_unchanged(before: str, after: str):
    """`origin/main` must not move between fetch and sweep, else UNKNOWN."""
    if not before or not after or before != after:
        return UNKNOWN
    return before


# ---------------------------------------------------------------------------
# Live reads (gh api only). Every one returns UNKNOWN on any doubt.
# ---------------------------------------------------------------------------

def _gh_api(path: str, paginate: bool = False):
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
    if paginate:
        # `--paginate` alone emits ONE JSON DOCUMENT PER PAGE for object
        # endpoints, which a single json.loads reads as "Extra data" -> UNKNOWN.
        # `--slurp` wraps the pages in one array; _merge_pages then concatenates
        # the page lists.
        cmd += ["--paginate", "--slurp"]
    cmd.append(path)
    last_status = None
    for _attempt in range(GH_API_ATTEMPTS):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            last_status = type(exc).__name__
            continue
        if proc.returncode != 0:
            last_status = f"exit {proc.returncode}: {proc.stderr.strip()[:200]}"
            continue
        body = parse_api_body(proc.stdout.encode())
        if body is UNKNOWN:
            last_status = "unparsable body"
            continue
        if paginate:
            body = _merge_pages(body)
        return body
    # Exhausted retries is UNKNOWN with the status RECORDED — never a partial
    # fallback (Task 1 Step 3).
    print(f"UNKNOWN: gh api {path} failed after {GH_API_ATTEMPTS} attempts "
          f"({last_status})", file=sys.stderr)
    return UNKNOWN


def _merge_pages(pages):
    """Merge `gh api --paginate --slurp` page objects into one document."""
    if not isinstance(pages, list) or not pages:
        return pages
    if all(isinstance(page, list) for page in pages):
        merged = []
        for page in pages:
            merged.extend(page)
        return merged
    if all(isinstance(page, dict) for page in pages):
        merged = dict(pages[0])
        for page in pages[1:]:
            for key, value in page.items():
                if isinstance(value, list) and isinstance(merged.get(key), list):
                    merged[key] = merged[key] + value
                elif key == "total_count" and _is_num(value) and _is_num(merged.get(key)):
                    merged[key] = max(merged[key], value)
                else:
                    merged[key] = value
        return merged
    return pages


def open_pr_total():
    """The INDEPENDENT open-PR count, from the pagination Link header.

    `_triage_rows` enumerates the population AND reconciles it against this, so
    a silently truncated page (the §8 integration-map failure) cannot validate
    clean — `len(rows) == len(body)` is a tautology, not a completeness check.
    UNKNOWN on any failure; never a guess. Residual, stated rather than
    hidden: this reconciles COUNTS, so a PR closing while another opens in the
    same window is not detected — the artifact is a point-in-time snapshot and
    records the `main_sha`/`generated_at` it was taken at.
    """
    path = f"repos/{OWNER_REPO}/pulls?state=open&per_page=1"
    cmd = ["gh", "api", "--include", "-H",
           "Accept: application/vnd.github+json", path]
    for _attempt in range(GH_API_ATTEMPTS):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        lines = proc.stdout.splitlines()
        if not lines or " 200 " not in lines[0]:
            continue
        for line in lines:
            if not line.lower().startswith("link:"):
                continue
            for part in line[5:].split(","):
                match = re.search(r'<([^>]+)>\s*;\s*rel="last"', part)
                if not match:
                    continue
                # PIN the endpoint STRUCTURALLY: the URL's PATH must be the
                # pulls collection (a substring `"/pulls?"` in a query value
                # was a false positive), and `page` must be a real query key
                # (not the first `page=` anywhere in the URL).
                try:
                    split = urllib.parse.urlsplit(match.group(1))
                    query = urllib.parse.parse_qs(split.query)
                except ValueError:
                    continue
                if not split.path.rstrip("/").endswith("/pulls"):
                    continue
                if "page" not in query:
                    continue
                try:
                    return int(query["page"][0])
                except (TypeError, ValueError):
                    continue
    print(f"UNKNOWN: could not read the open-PR total from {path}",
          file=sys.stderr)
    return UNKNOWN


def live_main_sha():
    try:
        fetch = subprocess.run(
            ["git", "fetch", "--no-tags", "origin", "main"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if fetch.returncode != 0:
            return UNKNOWN
        proc = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if proc.returncode != 0:
        return UNKNOWN
    sha = proc.stdout.strip()
    return sha or UNKNOWN


def api_main_sha():
    body = _gh_api(f"repos/{OWNER_REPO}/commits/main")
    if body is UNKNOWN or not isinstance(body, dict):
        return UNKNOWN
    return body.get("sha", UNKNOWN)


def workflow_of_for_sha(sha: str):
    """Return a resolver: check-run → workflow name, via the Actions API.

    A check-run's `details_url` carries its run id; the run id maps to the
    workflow name in ONE paginated call.
    """
    body = _gh_api(
        f"repos/{OWNER_REPO}/actions/runs?head_sha={sha}&per_page=100", paginate=True
    )
    if body is UNKNOWN or not isinstance(body, dict):
        return None
    mapping = {}
    for run in body.get("workflow_runs", []) or []:
        if isinstance(run, dict) and run.get("id") is not None:
            mapping[str(run["id"])] = run.get("name") or str(run["id"])

    def resolve(check_run: dict):
        url = check_run.get("details_url") or ""
        match = re.search(r"/runs/(\d+)", str(url))
        if match:
            return mapping.get(match.group(1))
        return None

    return resolve


def fetch_check_runs(sha: str):
    body = _gh_api(
        f"repos/{OWNER_REPO}/commits/{sha}/check-runs?filter=all&per_page=100",
        paginate=True,
    )
    if body is UNKNOWN or not isinstance(body, dict):
        return UNKNOWN, UNKNOWN
    runs = body.get("check_runs")
    if not isinstance(runs, list):
        return UNKNOWN, UNKNOWN
    return runs, body.get("total_count")


def required_contexts():
    body = _gh_api(f"repos/{OWNER_REPO}/branches/main/protection")
    if body is UNKNOWN or not isinstance(body, dict):
        return UNKNOWN
    checks = body.get("required_status_checks")
    if not isinstance(checks, dict):
        return UNKNOWN
    contexts = checks.get("contexts")
    if not isinstance(contexts, list):
        return UNKNOWN
    return [str(c) for c in contexts]


def _ensure_object(sha: str) -> bool:
    """Make `sha` available locally (fetch by sha when absent). False on doubt."""
    if not sha:
        return False
    try:
        check = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(REPO), capture_output=True, text=True, timeout=30, check=False,
        )
        if check.returncode == 0:
            return True
        fetch = subprocess.run(
            ["git", "fetch", "--no-tags", "origin", sha],
            cwd=str(REPO), capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return fetch.returncode == 0


def _failed_read(payload) -> bool:
    """A read that did not succeed or was not complete.

    `read_ok` must be exactly `True`; `incomplete_results` must be absent/False
    (a truthy string or `1` is a sentinel, not "complete").
    """
    return (
        payload.get("read_ok", True) is not True
        or payload.get("incomplete_results") not in (None, False)
    )


def collect_conflicts(bound=None):
    """Enumerate open PRs (complete) and sweep conflicts with git merge-tree."""
    body = _gh_api(
        f"repos/{OWNER_REPO}/pulls?state=open&per_page=100&sort=created&direction=asc",
        paginate=True,
    )
    if body is UNKNOWN:
        return {}
    if not isinstance(body, list):
        return {}
    main_sha = live_main_sha()
    if main_sha is UNKNOWN:
        return {}
    bound = validate_sweep_concurrency(bound)
    refs = [
        (p, p.get("head", {}).get("sha"))
        for p in body
        if isinstance(p, dict)
    ]
    probe_refs = [(p, sha) for p, sha in refs if sha]
    bounded_map(lambda item: _ensure_object(item[1]), probe_refs, bound)

    def probe(item):
        return merge_tree_conflict(REPO, "origin/main", item[1])

    results = bounded_map(probe, probe_refs, bound)
    after = live_main_sha()
    if assert_main_unchanged(main_sha, after) is UNKNOWN:
        return {"items": [], "total_count": 0, "read_ok": True,
                "main_moved": True}
    probed = {}
    for index, (pr, _sha) in enumerate(probe_refs):
        probed[id(pr)] = results[index]
    items = []
    for pr, sha in refs:
        # A PR without a head sha was not probed: it is UNKNOWN, never silently
        # dropped from the claimed population.
        conflict = probed.get(id(pr), UNKNOWN) if sha else UNKNOWN
        items.append({
            "number": pr.get("number"),
            "branch": pr.get("head", {}).get("ref"),
            "conflicting": conflict is True,
            "unknown": conflict is UNKNOWN,
        })
    return {"items": items, "total_count": len(items), "read_ok": True}


def collect_fast_files_unclassified():
    """Files in no manifest classification. Mirrors `ci_selection.integrity()`.

    Files are classified by their tests/-relative path (not basename) and the
    `tests/e2e/` prefix is skipped, exactly as the drift trap does (#5215).
    """
    try:
        sys.path.insert(0, str(REPO / "tools"))
        import ci_selection as cs

        manifest = cs.load_manifest()
        tests_dir = REPO / "tests"
        unclassified = []
        for path in sorted(tests_dir.rglob("test_*.py")):
            rel = path.relative_to(tests_dir)
            if rel.parts[0] == "e2e":
                continue
            if cs.classify_test_file(str(rel), manifest) is None:
                unclassified.append(str(rel))
        return {"fast_files_unclassified": unclassified}
    except Exception:
        return {}


def collect_payload(name: str, opts: dict):
    """Live read for a check when no fixture is injected."""
    try:
        if name == "conflicts":
            return collect_conflicts(opts.get("sweep_concurrency"))
        if name == "fast-files-unclassified":
            return collect_fast_files_unclassified()
        if name == "main-gate":
            sha = live_main_sha()
            if sha is UNKNOWN:
                return {}
            runs, total = fetch_check_runs(sha)
            if runs is UNKNOWN:
                return {}
            required = required_contexts()
            if required is UNKNOWN:
                # The required set is unreadable ⇒ the gate cannot be asserted.
                return {}
            resolver = workflow_of_for_sha(sha)
            if resolver is not None:
                for run in runs:
                    if isinstance(run, dict) and not run.get("workflow"):
                        with contextlib.suppress(Exception):
                            run["workflow"] = resolver(run)
            payload = {"check_runs": runs, "required": required, "sha": sha,
                       "live_main_sha": api_main_sha()}
            if _is_num(total):
                # Partial pagination is UNCONDITIONALLY non-zero (Task 1 Step 3).
                payload["total_count"] = total
            return payload
    except Exception:
        return {}
    return {}


# ---------------------------------------------------------------------------
# Checks. Each returns 0 / 1 / 2 and never 0 on an empty or unknown read.
# ---------------------------------------------------------------------------

def _strict_main_gate(runs, required) -> int:
    """STRICT polarity (S1): ONLY `success` is green.

    Delegates each required context to `mergify_mergeable` — the strict
    `check-success` polarity S1 names — so the emitted field is the one that
    decides. `neutral`/`skipped`/`cancelled`/`stale`/unknown/in-flight ⇒ 2 with
    the token named; a RED ⇒ 1; at least one OBSERVED `success` among the
    REQUIRED contexts is the non-vacuity guard. A required context with no
    check-run is `NO_MAIN_SIGNAL` — REPORTED and EXCLUDED (S1 cycle 7), never
    counted green.
    """
    if not required:
        print("2: required context set is empty/unreadable (nothing to assert)")
        return 2
    hard: list[str] = []
    soft: list[str] = []
    excluded: list[str] = []
    observed = 0
    for name in required:
        name = str(name)
        name_runs = [run for run in runs if str(run.get("name")) == name]
        if not name_runs:
            excluded.append(name)
            continue
        if mergify_mergeable(name_runs) == "MERGEABLE":
            observed += 1
            continue
        verdicts = [v for _, v in _verdicts(name_runs)]
        newest = _newest(name_runs)
        token = "unorderable" if newest is None else _conclusion_token(newest)
        if "RED" in verdicts:
            hard.append(f"{name}={token}")
        else:
            soft.append(f"{name}={token}")
    if excluded:
        print("NO_MAIN_SIGNAL (reported, excluded): " + ", ".join(excluded))
    if hard:
        print("1: required gate has a failing check: " + ", ".join(hard))
        return 1
    if soft:
        print("2: strict polarity (only success is green): " + ", ".join(soft))
        return 2
    if observed < 1:
        print("2: no required context observed success (all NO_MAIN_SIGNAL)")
        return 2
    return 0


def _check_main_gate(payload: dict, opts: dict) -> int:
    runs = payload.get("check_runs")
    if not isinstance(runs, list) or not runs:
        return 2
    total = payload.get("total_count")
    if not _is_num(total) or len(runs) != total:
        print(f"2: check-run enumeration is not reconcilable to a total "
              f"({len(runs)} of {total!r})")
        return 2
    if opts.get("require_fresh"):
        record_sha = payload.get("sha")
        live_sha = payload.get("live_main_sha")
        if not _is_sha(record_sha) or not _is_sha(live_sha) or record_sha != live_sha:
            print("2: main-gate evidence is not bound to the live main sha")
            return 2
    if opts.get("strict"):
        required = payload.get("required")
        return _strict_main_gate(runs, required if isinstance(required, list) else [])
    verdict = main_gate(runs)
    if verdict == "RED":
        print("1: lax main gate has a RED conclusion")
        return 1
    if verdict == "UNKNOWN":
        print("2: lax main gate is UNKNOWN")
        return 2
    if verdict == "NON_RED":
        print("0: lax main gate NON_RED (cancelled/stale allowed by the allow-list)")
    return 0


def _check_drain_rate(payload: dict, opts: dict) -> int:
    rate = _as_number(payload.get("merges_per_hour", UNKNOWN))
    if rate is None:
        print("2: merges_per_hour is UNKNOWN (not a zero rate)")
        return 2
    merges = _as_number(payload.get("merges", UNKNOWN))
    window = _as_number(payload.get("window_hours", UNKNOWN))
    if merges is None or window is None:
        return 2
    if opts.get("require_fresh") and not _age_ok(
        payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS
    ):
        print("2: drain window record is missing or stale")
        return 2
    if window < DRAIN_WINDOW_MIN_HOURS or merges < DRAIN_MIN_MERGES:
        print(f"2: drain window too weak (window={window}h, merges={merges})")
        return 2
    if window <= 0:
        return 2
    observed_rate = merges / window
    if abs(rate - observed_rate) > max(0.5, 0.1 * abs(observed_rate)):
        print(f"2: merges_per_hour {rate} does not reconcile with "
              f"{merges} merges / {window}h")
        return 2
    threshold = _as_number(opts.get("min"))
    if threshold is None:
        return 2
    return 0 if rate >= threshold else 1


def _check_prs_per_day(payload: dict, opts: dict) -> int:
    measured = _as_number(payload.get("prs_per_day", UNKNOWN))
    spec = opts.get("or_artifact")
    threshold = _as_number(opts.get("min"))
    fresh = opts.get("require_fresh")
    if measured is None:
        print("2: prs_per_day is UNKNOWN (a ceiling artifact does not measure it)")
        return 2
    if spec is not None and not spec:
        print("2: --or-artifact requires a PATH#ANCHOR value")
        return 2
    # S4 is a disjunction: "passes on the threshold OR a typed INTEGER ceiling".
    # The threshold branch must be evaluated first, or `--min` in the plan's own
    # S4 command is dead and the outcome S4 exists to certify reports a miss.
    if threshold is not None and measured >= threshold:
        if fresh and not _age_ok(payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS):
            print("2: prs-per-day snapshot is missing or stale")
            return 2
        return 0
    if spec:
        code, ceiling, section = _evaluate_ceiling(spec, payload)
        if code is not None:
            return code
        if fresh:
            stamps = _VERIFIED_RE.findall(section or "")
            stamp = stamps[0] if len(stamps) == 1 else payload.get("verified_at")
            if not _age_ok(stamp, DEFAULT_RECORD_WINDOW_DAYS):
                print("2: ceiling artifact is missing a fresh verified_at")
                return 2
        if measured > ceiling:
            print(f"1: measured {measured}/day exceeds the documented ceiling {ceiling}")
            return 1
        return 0
    if threshold is None:
        return 2
    if fresh and not _age_ok(payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS):
        print("2: prs-per-day snapshot is missing or stale")
        return 2
    return 1


_CEILING_RE = re.compile(r"^\s*ceiling_prs_per_day\s*:\s*(.*?)\s*$", re.M)
_SOURCE_RE = re.compile(r"^\s*ceiling_source\s*:\s*(.*?)\s*$", re.M)
_VERIFIED_RE = re.compile(r"^\s*verified_at\s*:\s*(.*?)\s*$", re.M)


def _evaluate_ceiling(spec: str, payload: dict):
    """Typed INTEGER ceiling from an anchored section. `(None, value, section)` on ok."""
    if "#" not in spec:
        return 2, None, None
    path_part, anchor = spec.rsplit("#", 1)
    path = Path(path_part)
    if not path.is_absolute():
        path = REPO / path
    try:
        text = path.read_text()
    except OSError:
        print(f"2: artifact unreadable: {path}")
        return 2, None, None
    section = _section(text, anchor)
    if section is None:
        print(f"2: anchor #{anchor} not found in {path}")
        return 2, None, None
    ceiling_matches = _CEILING_RE.findall(section)
    if len(ceiling_matches) != 1:
        print(f"2: ceiling_prs_per_day must appear exactly once ({len(ceiling_matches)})")
        return 2, None, None
    raw = ceiling_matches[0]
    if not re.fullmatch(r"\d+", raw):
        print(f"2: ceiling_prs_per_day must be a typed integer, got {raw!r}")
        return 2, None, None
    ceiling = int(raw)
    if not (0 < ceiling <= CEILING_MAX):
        return 2, None, None
    source_matches = _SOURCE_RE.findall(section)
    if len(source_matches) != 1 or not source_matches[0]:
        print("2: ceiling_source must name the .gap.terms keys")
        return 2, None, None
    sources = [s.strip() for s in source_matches[0].split(",") if s.strip()]
    emitted = _emitted_terms(payload)
    if emitted is None:
        print("2: --or-artifact requires the emitted .gap.terms "
              "(a self-set integer is not a derived ceiling)")
        return 2, None, None
    values = {}
    for key in sources:
        if key not in emitted or not _is_num(emitted[key]):
            print(f"2: ceiling_source names {key!r} absent from .gap.terms")
            return 2, None, None
        values[key] = emitted[key]
    for needed in ("effective_parallel", "effective_batch", "cycle_minutes"):
        if needed not in values:
            print(f"2: ceiling is not derivable — missing {needed!r}")
            return 2, None, None
    if not _is_num(values.get("effective_parallel")) or values["effective_parallel"] <= 0:
        print("2: effective_parallel must be a positive number in .gap.terms")
        return 2, None, None
    if not _is_num(values.get("effective_batch")) or values["effective_batch"] <= 0:
        print("2: effective_batch must be a positive number in .gap.terms")
        return 2, None, None
    cycle = values["cycle_minutes"]
    if not _is_num(cycle) or cycle <= 0:
        print("2: cycle_minutes must be a positive number in MINUTES")
        return 2, None, None
    derived = round(values["effective_parallel"] * values["effective_batch"] * 1440 / cycle)
    tolerance = max(1, round(derived * CEILING_TOLERANCE))
    if abs(ceiling - derived) > tolerance:
        print(f"2: ceiling {ceiling} does not reconcile with derived {derived} (±10%)")
        return 2, None, None
    return None, ceiling, section


def _emitted_terms(payload: dict):
    terms = payload.get("gap", {}).get("terms") if isinstance(payload.get("gap"), dict) else None
    if not isinstance(terms, dict):
        return None
    out = {}
    for key, term in terms.items():
        if isinstance(term, dict) and _is_num(term.get("value")):
            out[key] = term["value"]
    return out


def _section(text: str, anchor: str):
    """Return the body of the section whose heading names `anchor`."""
    if not anchor:
        return None
    pattern = re.compile(rf"^(#+)\s*{re.escape(anchor)}\s*$")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if pattern.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return None
    body = []
    for line in lines[start:]:
        if re.match(r"^#+\s", line.strip()) and line.strip():
            break
        body.append(line)
    return "\n".join(body)


def _check_queue_entry(payload: dict, opts: dict) -> int:
    want = _as_int(opts.get("pr"))
    if want is not None and _as_int(payload.get("pr")) != want:
        print("2: queue-entry evidence is for a different PR (--pr scoping)")
        return 2
    entered = payload.get("entered_queue")
    trigger = payload.get("trigger")
    if entered is None or trigger is None:
        return 2
    if not isinstance(entered, bool):
        print(f"2: entered_queue is {entered!r}, not a boolean")
        return 2
    if entered is False:
        # An observed "not entered" is the miss S2 exists to detect; only an
        # unobserved (absent/non-boolean) value is UNKNOWN.
        print("1: queue entry was never observed on this PR's head")
        return 1
    if trigger in (None, UNKNOWN):
        print("2: queue-entry trigger was never observed")
        return 2
    if trigger != "auto_merge_conditions":
        print(f"1: queue entry was not unaided (trigger={trigger!r})")
        return 1
    if opts.get("require_fresh"):
        head = payload.get("head_sha")
        run_sha = payload.get("run_sha")
        if not _is_sha(head) or not _is_sha(run_sha) or head != run_sha:
            print("2: queue-entry evidence is not on the PR's current head")
            return 2
        if not _age_ok(payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS):
            return 2
    return 0


def _check_queue_eta(payload: dict, opts: dict) -> int:
    if _failed_read(payload):
        return 2
    items = payload.get("items")
    total = payload.get("total_count")
    min_depth = _as_int(opts.get("min_depth"))
    needs_population = min_depth is not None or opts.get("require_complete")
    # The population floor must be backed by a reconciled read: a claimed
    # total_count with no items is not an observation.
    if needs_population and (
        not isinstance(items, list)
        or not _is_num(total)
        or len(items) != total
        or total < 1
    ):
        print("2: ETA enumeration did not reconcile to its total")
        return 2
    if min_depth is not None:
        population = total if _is_num(total) else None
        if population is None or population < min_depth:
            print("2: ETA population below --min-depth")
            return 2
    value = _as_number(payload.get("max_eta_minutes", UNKNOWN))
    observed = None
    if isinstance(items, list) and items:
        if any(not isinstance(item, dict) for item in items):
            print("2: queue-eta items are mis-shaped")
            return 2
        etas = [_as_number(item.get("eta_minutes")) for item in items]
        if any(eta is None or eta < 0 for eta in etas):
            # A population with ANY unobserved ETA cannot be vouched for by a
            # self-set maximum: S5's fail direction is an under-reported max.
            print("2: queue-eta population has non-numeric or negative ETAs")
            return 2
        observed = max(etas)
        if value is not None and abs(value - observed) > 1e-9:
            print(f"2: max_eta_minutes {value} does not reconcile with items ({observed})")
            return 2
    if value is None:
        value = observed
    if value is None:
        return 2
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        return 2
    return 0 if value <= threshold else 1


def _check_batch_size(payload: dict, opts: dict) -> int:
    events = _as_number(payload.get("events", UNKNOWN))
    sizes = payload.get("batch_sizes")
    if events is None or not isinstance(sizes, list):
        return 2
    if events != len(sizes):
        # The window count and the observed list must reconcile, else the
        # claimed max is taken over an unobserved subset of events.
        print(f"2: batch window claims {events} events but carries {len(sizes)} sizes")
        return 2
    if opts.get("require_fresh") and not _age_ok(
        payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS
    ):
        return 2
    min_depth = _as_int(opts.get("min_depth"))
    if min_depth is not None and events < min_depth:
        print("2: batch window holds too few batch events")
        return 2
    value = _as_number(payload.get("max_batch_size", UNKNOWN))
    numeric = [_as_number(s) for s in sizes]
    if any(n is None or n < 0 for n in numeric):
        # Every batch event in the window must be observed; a dropped entry
        # would reconcile the claimed max against a subset.
        print("2: batch_sizes has non-numeric or negative entries")
        return 2
    if value is not None and not numeric:
        # Nothing to reconcile against: a self-set max over an empty/opaque
        # batch-event window is not an observation (S6 derives from events).
        print("2: max_batch_size has no observed batch_sizes to reconcile with")
        return 2
    if value is not None and abs(value - max(numeric)) > 1e-9:
        print(f"2: max_batch_size {value} does not reconcile with batch_sizes")
        return 2
    if value is None:
        if not numeric:
            return 2
        value = max(numeric)
    threshold = _as_number(opts.get("min"))
    if threshold is None:
        return 2
    return 0 if value >= threshold else 1


def _check_cycle(payload: dict, opts: dict) -> int:
    runs = payload.get("runs")
    if not isinstance(runs, list):
        return 2
    qualifying = [
        r.get("duration_minutes")
        for r in runs
        if isinstance(r, dict)
        and r.get("heavy_leg_conclusion") == "success"
        and _is_num(r.get("duration_minutes"))
        and r.get("duration_minutes") >= 0
    ]
    min_depth = _as_int(opts.get("min_depth"))
    if min_depth is not None and len(qualifying) < min_depth:
        print(f"2: only {len(qualifying)} qualifying runs (< --min-depth {min_depth})")
        return 2
    if not qualifying:
        return 2
    value = _as_number(payload.get("median_minutes", UNKNOWN))
    observed = _median(qualifying)
    if value is not None and observed is not None and abs(value - observed) > 1e-9:
        print(f"2: median_minutes {value} does not reconcile with runs ({observed})")
        return 2
    if value is None:
        value = observed
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        return 2
    if opts.get("require_fresh") and not _age_ok(
        payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS
    ):
        return 2
    return 0 if value <= threshold else 1


def _check_shard_balance(payload: dict, opts: dict) -> int:
    legs = payload.get("legs")
    if not isinstance(legs, dict):
        return 2
    for leg_name in ("a", "b"):
        leg = legs.get(leg_name)
        if not isinstance(leg, dict) or leg.get("conclusion") != "success":
            print(f"2: heavy leg {leg_name!r} absent or not success")
            return 2
    value = _as_number(payload.get("shard_imbalance_minutes", UNKNOWN))
    if value is None or value < 0:
        return 2
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        return 2
    return 0 if value <= threshold else 1


def _check_fast_files_unclassified(payload: dict, opts: dict) -> int:
    files = payload.get("fast_files_unclassified")
    if not isinstance(files, list):
        return 2
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        threshold = 0
    return 0 if len(files) <= threshold else 1


def _check_conflicts(payload: dict, opts: dict) -> int:
    if _failed_read(payload):
        return 2
    if payload.get("main_moved"):
        print("2: origin/main moved between fetch and sweep")
        return 2
    items = payload.get("items")
    total = payload.get("total_count")
    if not isinstance(items, list) or not _is_num(total):
        return 2
    if len(items) != total or total < 1:
        print("2: conflict enumeration did not reconcile to its total")
        return 2
    floor = MIN_OPEN_PR_POPULATION
    raised = opts.get("min_population")
    if raised not in (None, "", UNKNOWN):
        raised_int = _as_int(raised)
        if raised_int is None:
            return 2
        floor = max(floor, raised_int)
    if total < floor:
        print(f"2: enumerated population {total} below the floor {floor}")
        return 2
    if opts.get("require_complete") and len(items) != total:
        return 2
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("conflicting"), bool)
        for item in items
    ):
        print("2: conflict items carry no probe verdict (mis-shaped read)")
        return 2
    conflicting = sum(
        1 for item in items if isinstance(item, dict) and item.get("conflicting")
    )
    unknown = sum(
        1 for item in items if isinstance(item, dict) and item.get("unknown")
    )
    if unknown:
        print(f"2: {unknown} branches could not be probed (UNKNOWN)")
        return 2
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        threshold = 5
    return 0 if conflicting <= threshold else 1


def _check_attribution(payload: dict, opts: dict) -> int:
    want = _as_int(opts.get("pr"))
    if want is not None and _as_int(payload.get("pr")) != want:
        print("2: attribution evidence is for a different PR (--pr scoping)")
        return 2
    if not isinstance(payload.get("main_red"), bool):
        return 2
    if not payload["main_red"]:
        print("2: no main red attributed to this PR (not applicable)")
        return 2
    first = _parse_ts(payload.get("red_first_observed"))
    recorded = _parse_ts(payload.get("attribution_recorded"))
    if first is None or recorded is None:
        return 2
    delta = (recorded - first).total_seconds() / 60.0
    if delta < 0:
        return 2
    if opts.get("require_fresh") and not _age_ok(
        payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS
    ):
        return 2
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        return 2
    return 0 if delta <= threshold else 1


def _headroom(payload: dict):
    cap = _as_number(payload.get("capacity_at_first_failure"))
    configured = _as_number(payload.get("configured_max_parallel_checks"))
    if cap is None or configured is None:
        return None, None
    return cap - configured, configured


def _check_capacity(payload: dict, opts: dict) -> int:
    if not payload:
        return 2
    for key in ("queued", "in_progress", "oldest_minutes",
                "capacity_at_first_failure", "configured_max_parallel_checks"):
        if not _is_num(payload.get(key)):
            print(f"2: capacity field {key!r} is missing or not numeric")
            return 2
        if payload.get(key) < 0:
            print(f"2: capacity field {key!r} is negative")
            return 2
    if opts.get("require_complete"):
        items = payload.get("items")
        total = payload.get("total_count")
        if not isinstance(items, list) or not _is_num(total) or len(items) != total or total < 1:
            print("2: capacity enumeration did not reconcile to its total")
            return 2
    if opts.get("require_fresh"):
        records = payload.get("records")
        if not isinstance(records, dict):
            print("2: capacity --require-fresh needs each field's own record")
            return 2
        for field in _CAPACITY_FRESH_FIELDS:
            if not _fresh_record(records.get(field)):
                print(f"2: capacity field {field!r} has no fresh own record")
                return 2
    oldest = _as_number(payload.get("oldest_minutes"))
    if oldest is None:
        return 2
    max_oldest = _as_number(opts.get("max_oldest_minutes"))
    if max_oldest is not None and oldest > max_oldest:
        print(f"1: oldest queued age {oldest} > {max_oldest} minutes")
        return 1
    min_headroom = _as_number(opts.get("min_headroom"))
    if min_headroom is not None:
        headroom, _configured = _headroom(payload)
        if headroom is None:
            print("2: headroom is UNKNOWN (no capacity_at_first_failure sample)")
            return 2
        if headroom < min_headroom:
            print(f"1: headroom {headroom} < required {min_headroom}")
            return 1
    return 0


def _check_parallelism_headroom(payload: dict, opts: dict) -> int:
    if opts.get("require_complete"):
        items = payload.get("items")
        total = payload.get("total_count")
        if (not isinstance(items, list) or not _is_num(total)
                or len(items) != total or total < 1):
            print("2: parallelism-headroom enumeration did not reconcile to its total")
            return 2
    if opts.get("require_fresh"):
        # S11/S14 must agree on the same headroom: the per-field record rule is
        # the same one `capacity --require-fresh` applies.
        records = payload.get("records")
        if not isinstance(records, dict):
            print("2: parallelism-headroom --require-fresh needs each field's own record")
            return 2
        for field in _CAPACITY_FRESH_FIELDS:
            if not _fresh_record(records.get(field)):
                print(f"2: parallelism-headroom field {field!r} has no fresh own record")
                return 2
    cap = _as_number(payload.get("capacity_at_first_failure"))
    configured = _as_number(payload.get("configured_max_parallel_checks"))
    if cap is None or configured is None or cap < 0 or configured < 0:
        print("2: capacity_at_first_failure is UNKNOWN (refuse, never pass)")
        return 2
    # I9: refusal condition is configured >= capacity_at_first_failure.
    return 1 if configured >= cap else 0


def _check_gap(payload: dict, opts: dict) -> int:
    gap = payload.get("gap")
    if not isinstance(gap, dict):
        return 2
    terms = gap.get("terms")
    if not isinstance(terms, dict) or len(terms) < GAP_TERM_MIN:
        print(f"2: .gap.terms must carry at least {GAP_TERM_MIN} terms")
        return 2
    missing = sorted(_GAP_TERMS - set(terms))
    if missing:
        print(f"2: .gap.terms is missing the named decomposition term(s) {missing}")
        return 2
    for key, term in terms.items():
        if not isinstance(term, dict):
            return 2
        if not _is_num(term.get("value")):
            print(f"2: gap term {key!r} is not numeric")
            return 2
        if not term.get("source") or term.get("source") == UNKNOWN:
            print(f"2: gap term {key!r} carries no source")
            return 2
    parallel = terms.get("effective_parallel")
    if not isinstance(parallel, dict) or parallel.get("source") != "M3":
        print("2: .gap.terms.effective_parallel.source != 'M3'")
        return 2
    if opts.get("require_fresh"):
        record_path = parallel.get("record")
        if not record_path:
            print("2: effective_parallel has no named persisted record")
            return 2
        if not _age_ok(parallel.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS):
            return 2
        # The value must be READ FROM the named persisted record on disk. An
        # inline copy is never a substitute for the file (S13: a source with
        # no record on disk is 2).
        try:
            resolved = (REPO / str(record_path)).resolve()
            resolved.relative_to(REPO.resolve())
        except (ValueError, OSError):
            print("2: M3 record path escapes the repo")
            return 2
        if not resolved.is_file():
            print(f"2: M3 record {record_path!r} is not on disk")
            return 2
        try:
            record = jsonlib.loads(resolved.read_text())
        except (OSError, ValueError):
            return 2
        if not isinstance(record, dict) or not _age_ok(
            record.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS
        ):
            return 2
        # The term's VALUE must be read from that record (S13) — a fabricated
        # inline value is not made true by a fresh file existing.
        record_value = record.get("value")
        if record_value is None and "effective_parallel" in record:
            record_value = record.get("effective_parallel")
        if not _is_num(record_value) or abs(
            parallel.get("value") - record_value
        ) > 1e-9:
            print("2: effective_parallel value does not match its M3 record")
            return 2
    value = _as_number(gap.get("value", UNKNOWN))
    if value is None:
        return 2
    ceiling_term = terms.get("ceiling")
    observed_term = terms.get("observed")
    if not (
        isinstance(ceiling_term, dict)
        and isinstance(observed_term, dict)
        and _is_num(ceiling_term.get("value"))
        and _is_num(observed_term.get("value"))
    ):
        print("2: .gap.terms must carry numeric `ceiling` and `observed`")
        return 2
    if observed_term["value"] <= 0 or ceiling_term["value"] <= 0:
        print("2: .gap.terms ceiling/observed must be positive")
        return 2
    derived = ceiling_term["value"] / observed_term["value"]
    if abs(value - derived) > GAP_VALUE_TOLERANCE * abs(derived):
        print(f"2: .gap.value {value} does not reconcile with ceiling/observed "
              f"({derived})")
        return 2
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        return 2
    return 0 if value <= threshold else 1


def _check_no_languish(payload: dict, opts: dict) -> int:
    excludes = opts.get("exclude")
    if excludes is None:
        excludes = []
    if isinstance(excludes, str):
        excludes = [e.strip() for e in excludes.split(",") if e.strip()]
    for key in excludes:
        if key not in EXCLUDE_KEYS:
            print(f"2: unknown exclude key {key!r}")
            return 2
    if _failed_read(payload):
        return 2
    items = payload.get("items")
    total = payload.get("total_count")
    if not isinstance(items, list) or not _is_num(total):
        return 2
    if len(items) != total or total < 1:
        return 2
    if opts.get("require_fresh") and not _age_ok(
        payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS
    ):
        print("2: triage snapshot is missing or stale")
        return 2
    floor = MIN_OPEN_PR_POPULATION
    raised = opts.get("min_population")
    if raised not in (None, "", UNKNOWN):
        raised_int = _as_int(raised)
        if raised_int is None:
            return 2
        floor = max(floor, raised_int)
    if total < floor:
        print(f"2: enumerated population {total} below the floor {floor}")
        return 2
    if opts.get("require_complete") and len(items) != total:
        return 2
    if any(not isinstance(row, dict) for row in items):
        print("2: no-languish items are mis-shaped")
        return 2
    # An S15 record must state the window `moved_in_window` was judged over —
    # otherwise the boolean is unverifiable and the artifact can be hand-written
    # all-true. A Task-5-schema record (a classification column) MUST state it;
    # a legacy record that carries one must also have it be sensible.
    task5_schema = any("bucket" in row or "classification" in row
                       or "conflict" in row for row in items)
    if task5_schema or "window_days" in payload:
        window_days = _as_number(payload.get("window_days"))
        if window_days is None or window_days <= 0:
            print("2: no-languish window_days is missing or non-positive")
            return 2
    # A boolean may only mark a row into the FIRST-MATCH bucket it names: a row
    # that sets `hard_stop`/`terminal_decision`/`draft` true while its bucket is
    # a LOWER-precedence bucket is inconsistent, and must not be excluded on the
    # strength of that flag (a higher-precedence bucket may legitimately carry a
    # true flag — a hard_stop PR can also be a draft).
    rank = {name: index for index, name in enumerate(BUCKET_ORDER)}
    sup_unknown = (None, "", "null", UNKNOWN)
    for row in items:
        # TWO classification columns that disagree are an unvalidated SECOND
        # exclusion channel: `bucket` says eligible, `classification` says
        # draft, and the row is hidden. Refuse the disagreement.
        if (row.get("bucket") is not None
                and row.get("classification") is not None
                and row["bucket"] != row["classification"]):
            print(f"2: no-languish row {row.get('number')} bucket "
                  f"{row['bucket']!r} != classification {row['classification']!r}")
            return 2
        for key in ("hard_stop", "terminal_decision", "draft"):
            if key in row and not isinstance(row[key], bool):
                print(f"2: no-languish row {key!r} is not boolean")
                return 2
        for key in ("bucket", "classification"):
            if key in row and not isinstance(row[key], str):
                print(f"2: no-languish row {key!r} is not a string")
                return 2
        superseded = row.get("superseded_by")
        if superseded is not None and not isinstance(superseded, str):
            print("2: no-languish superseded_by is mis-shaped")
            return 2
        # `superseded_by` is INDEPENDENT evidence (categories overlap), but it
        # only makes sense on a bucket that PRECEDES dead_weight in first-match
        # order: a marker on draft/conflicting/eligible is a record whose
        # first-match bucket should have been dead_weight. Refuse it (exit 2)
        # rather than let either column decide the row.
        if (superseded not in sup_unknown and row.get("bucket") in rank
                and rank[row["bucket"]] > rank["dead_weight"]):
            print(f"2: no-languish row {row.get('number')} carries superseded_by "
                  f"but its bucket {row['bucket']!r} follows dead_weight")
            return 2
        label = row.get("bucket", row.get("classification"))
        # The label is SELF-DECLARED. Verify it is the first match of the row's
        # OWN columns before excluding on it — otherwise a row can declare
        # `bucket:"draft"` (or `classification:"draft"`) with `draft:false`
        # and hide a moveless PR.
        if "bucket" in row:
            row_surface, row_stale = surface_from_evidence(
                row.get("surface_evidence"))
            expected = classify_bucket(
                row.get("number"), draft=row.get("draft"),
                conflict=row.get("conflict"), surface=row_surface,
                stale=row_stale, hard_stop=HARD_STOP_D12,
                terminal=TERMINAL_D5, dead_weight=DEAD_WEIGHT_EVIDENCE)
            if row["bucket"] != expected:
                print(f"2: no-languish row {row.get('number')} bucket "
                      f"{row['bucket']!r} is not the first match ({expected!r})")
                return 2
        for key in ("hard_stop", "terminal_decision", "draft"):
            if (row.get(key) is True and label in rank
                    and rank[label] > rank[key]):
                print(f"2: no-languish row {row.get('number')} sets {key}=true "
                      f"but its bucket is {label!r}")
                return 2
    excluded = set(excludes)

    def _excluded(row: dict) -> bool:
        # EXCLUSION KEYS OFF ONE CLASSIFICATION COLUMN ONLY — never the union of
        # both (a disagreeing `classification` was a second hiding channel), and
        # never a bare boolean. `bucket` is authoritative when present.
        label = row.get("bucket", row.get("classification"))
        labels = {str(label)} if label is not None else set()
        for key in excluded:
            if key == "superseded_by":
                value = row.get("superseded_by")
                if value not in sup_unknown and row.get("bucket") == "dead_weight":
                    return True
            elif key in labels:
                return True
        return False

    active = [row for row in items if isinstance(row, dict) and not _excluded(row)]
    if not active:
        print("2: every row is in an excluded state")
        return 2
    if any(not isinstance(row.get("moved_in_window"), bool) for row in active):
        print("2: no-languish active rows carry no boolean moved_in_window")
        return 2
    languishing = [row for row in active if not row.get("moved_in_window")]
    threshold = _as_number(opts.get("max"))
    if threshold is None:
        threshold = 0
    return 0 if len(languishing) <= threshold else 1


def _check_baseline_fresh(payload: dict, opts: dict) -> int:
    window = _as_number(opts.get("max_age_days"))
    if window is None:
        window = DEFAULT_RECORD_WINDOW_DAYS
    if _age_ok(payload.get("verified_at", UNKNOWN), window):
        return 0
    age = _age_days(payload.get("verified_at", UNKNOWN))
    if age is None or age < 0:
        return 2
    return 1


def _check_assert_queue_head_checks(payload: dict, opts: dict) -> int:
    head = payload.get("queue_head")
    if not head or head is UNKNOWN or not isinstance(head, str):
        print("2: no mergify/merge-queue/* head exists")
        return 2
    required = payload.get("required")
    names = payload.get("names")
    if not isinstance(required, list) or not isinstance(names, list):
        return 2
    if not required:
        print("2: required context set is empty (nothing to assert)")
        return 2
    missing = [name for name in required if name not in names]
    if missing:
        print(f"1: queue head is missing check(s): {missing}")
        return 1
    return 0


def _check_durations_map(payload: dict, opts: dict) -> int:
    if _failed_read(payload):
        print("2: jobs read was empty/0-byte or incomplete")
        return 2
    dm = payload.get("durations_map")
    if not isinstance(dm, dict):
        return 2
    sampled = _as_number(dm.get("sampled_keys", UNKNOWN))
    if sampled is None or sampled < 1:
        print("2: durations projection is empty (sampled_keys < 1)")
        return 2
    age = _as_number(dm.get("age_days", UNKNOWN))
    if age is None or age < 0:
        return 2
    window = _as_number(opts.get("max_age_days"))
    if window is None:
        return 2
    diverged = payload.get("diverged")
    if not isinstance(diverged, list):
        # An absent comparison is an UNKNOWN, never "no divergence" (#3395).
        print("2: durations-map divergence comparison was not performed")
        return 2
    if age > window:
        print(f"1: durations map age {age}d > {window}d")
        return 1
    if diverged:
        print(f"1: durations keys diverged: {diverged}")
        return 1
    return 0


_DISPATCH = {
    "main-gate": _check_main_gate,
    "drain-rate": _check_drain_rate,
    "prs-per-day": _check_prs_per_day,
    "queue-entry": _check_queue_entry,
    "queue-eta": _check_queue_eta,
    "batch-size": _check_batch_size,
    "cycle": _check_cycle,
    "shard-balance": _check_shard_balance,
    "fast-files-unclassified": _check_fast_files_unclassified,
    "conflicts": _check_conflicts,
    "attribution": _check_attribution,
    "capacity": _check_capacity,
    "parallelism-headroom": _check_parallelism_headroom,
    "gap": _check_gap,
    "no-languish": _check_no_languish,
    "baseline-fresh": _check_baseline_fresh,
    "assert-queue-head-checks": _check_assert_queue_head_checks,
    "durations-map": _check_durations_map,
}


def run_check(name: str, json=None, **opts) -> int:
    """Run one check. Returns 0 / 1 / 2. Never 0 on empty or unknown input."""
    if name not in CHECK_NAMES:
        print(f"2: unknown check {name!r}")
        return 2
    if opts.get("require_fresh") and name not in _FRESHNESS_CHECKS:
        # Accept-and-ignore would let a stale record pass while the flag reads
        # as an enforced freshness gate.
        print(f"2: {name} implements no freshness test; --require-fresh is refused")
        return 2
    payload = json
    if payload is None:
        payload = collect_payload(name, opts)
    if not isinstance(payload, dict):
        print(f"2: {name}: no parsable data (UNKNOWN)")
        return 2
    try:
        return _DISPATCH[name](payload, opts)
    except Exception as exc:  # fail closed: a crash is UNKNOWN, never a miss
        print(f"2: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# --json report — emit fields, never a judgement.
# ---------------------------------------------------------------------------

def _fixture_gap(value: float = 2.0):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017
    ceiling = 100
    observed = round(ceiling / value, 6)
    return {
        "gap": {
            "value": value,
            "terms": {
                "ceiling": {"value": ceiling, "source": "S4"},
                "observed": {"value": observed, "source": "M1"},
                "effective_parallel": {
                    "value": 3, "source": "M3",
                    "record": "docs/ci/measurements.json", "verified_at": now,
                },
                "effective_batch": {"value": 2, "source": "M5"},
                "cycle_minutes": {"value": 37, "source": "M2"},
                "wait": {"value": 1, "source": "M6"},
            },
        },
    }


_FIXTURES = {
    "empty": {},
    "gap_ok": {
        "gap": _fixture_gap(),
        "fast-files-unclassified": {"fast_files_unclassified": []},
    },
    "conjunct_mixed": {
        "gap": _fixture_gap(3.0),
        "fast-files-unclassified": {"fast_files_unclassified": []},
    },
    "conjunct_unknown": {
        "gap": _fixture_gap(3.0),
        "fast-files-unclassified": {},
    },
}

_FIXTURE_TRIAGE_ROWS = [{
    "number": 1,
    "bucket": "eligible",
    "eligible": True,
    "conflict": False,
    "conflicted_paths": [],
    "superseded_by": UNKNOWN,
    "surface_evidence": "verdict=GREEN; stale=0; produced=2026-09-27T00:00:00Z",
    "draft": False,
    "hard_stop": False,
    "terminal_decision": False,
    "owner": UNKNOWN,
    "owner_evidence": UNKNOWN,
    "owning_issue": UNKNOWN,
}]


def _fixture_payload(fixture: str, name: str):
    return _FIXTURES.get(fixture, {}).get(name, {})


def build_report(fixture=None):
    """Emit all report fields. With a fixture, no network is touched."""
    if fixture is not None:
        sha = UNKNOWN
        main_runs = _fixture_payload(fixture, "main-gate").get("check_runs")
        conflicts = _fixture_payload(fixture, "conflicts")
        gap = _fixture_payload(fixture, "gap").get("gap", {"value": UNKNOWN, "terms": {}})
        capacity = _fixture_payload(fixture, "capacity")
        dm = _fixture_payload(fixture, "durations-map").get("durations_map", {})
        ff = _fixture_payload(fixture, "fast-files-unclassified")
        batch = _fixture_payload(fixture, "batch-size")
    else:
        sha = live_main_sha()
        main_runs, _ = fetch_check_runs(sha) if sha is not UNKNOWN else ({}, None)
        if main_runs is UNKNOWN:
            main_runs = []
        conflicts = collect_conflicts()
        gap = {"value": UNKNOWN, "terms": {}}
        capacity = {}
        dm = {}
        ff = collect_fast_files_unclassified()
        batch = {}
    runs = main_runs or []
    return {
        "main_sha": sha,
        "live_main_sha": api_main_sha() if fixture is None else UNKNOWN,
        "main_gate": main_gate(runs) if runs else UNKNOWN,
        "mergify_mergeable": mergify_mergeable(runs) if runs else UNKNOWN,
        "max_batch_size": batch.get("max_batch_size", UNKNOWN),
        "shard_imbalance_minutes": UNKNOWN,
        "queue_depth": UNKNOWN,
        "fast_files_unclassified": ff.get("fast_files_unclassified", UNKNOWN),
        "conflicts": {
            "total": conflicts.get("total_count", UNKNOWN),
            "items": conflicts.get("items", []),
        },
        "gap": gap,
        "capacity": {
            "queued": capacity.get("queued", UNKNOWN),
            "in_progress": capacity.get("in_progress", UNKNOWN),
            "oldest_minutes": capacity.get("oldest_minutes", UNKNOWN),
            "headroom": (
                _headroom(capacity)[0] if _headroom(capacity)[0] is not None else UNKNOWN
            ),
        },
        "durations_map": {
            "age_days": dm.get("age_days", UNKNOWN),
            "sampled_keys": dm.get("sampled_keys", UNKNOWN),
            "tolerance": dm.get("tolerance", UNKNOWN),
        },
    }


_MISSING = object()


def _extract_path(report: dict, path: str):
    """Return the report value at `path`, or `_MISSING` when it is absent.

    `_MISSING` is deliberately distinct from `UNKNOWN`: a path that does not
    exist was never read, and `--json <path>` must exit 2 for it rather than
    print an UNKNOWN that reads as a real (if unsupported) field.
    """
    if path in (None, "", "."):
        return report
    parts = [p for p in str(path).strip(".").split(".") if p]
    current = report
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

_VALUE_FLAGS = {
    "--min": "min",
    "--max": "max",
    "--pr": "pr",
    "--min-depth": "min_depth",
    "--min-population": "min_population",
    "--exclude": "exclude",
    "--or-artifact": "or_artifact",
    "--max-oldest-minutes": "max_oldest_minutes",
    "--min-headroom": "min_headroom",
    "--max-age-days": "max_age_days",
    "--fixture": "fixture",
    "--input": "input",
    "--sweep-concurrency": "sweep_concurrency",
}
_NUMERIC_FLAGS = frozenset({
    "min", "max", "pr", "min_depth", "min_population",
    "max_oldest_minutes", "min_headroom", "max_age_days", "sweep_concurrency",
})
_BOOL_FLAGS = {
    "--strict": "strict",
    "--require-complete": "require_complete",
    "--require-fresh": "require_fresh",
}


def _parse_check_argv(argv):
    segments = []
    current_name = None
    current_opts: dict = {}
    fixture = None
    input_path = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--and":
            if i + 1 >= len(argv):
                return None, 2
            if current_name is not None:
                segments.append((current_name, current_opts))
            current_name = argv[i + 1]
            current_opts = {}
            i += 2
            continue
        if token in _VALUE_FLAGS:
            if i + 1 >= len(argv):
                return None, 2
            value = argv[i + 1]
            key = _VALUE_FLAGS[token]
            if key in _NUMERIC_FLAGS:
                # An unset/empty/garbage shell value must refuse, not silently
                # disable the gate it was meant to arm.
                if value == "" or value.startswith("--") or _as_number(value) is None:
                    print(f"2: {token} requires a numeric value, got {value!r}",
                          file=sys.stderr)
                    return None, 2
            elif key == "or_artifact" and value == "":
                print("2: --or-artifact requires a PATH#ANCHOR value", file=sys.stderr)
                return None, 2
            elif key in ("fixture", "input") and value == "":
                print(f"2: {token} requires a value", file=sys.stderr)
                return None, 2
            if key == "fixture":
                fixture = value
            elif key == "input":
                input_path = value
            else:
                current_opts[key] = value
            i += 2
            continue
        if token in _BOOL_FLAGS:
            current_opts[_BOOL_FLAGS[token]] = True
            i += 1
            continue
        if current_name is None and not token.startswith("--"):
            current_name = token
            i += 1
            continue
        print(f"2: unrecognised argument {token!r}", file=sys.stderr)
        return None, 2
    if current_name is not None:
        segments.append((current_name, current_opts))
    if not segments:
        return None, 2
    return (segments, fixture, input_path), None


def _fixture_allowed() -> bool:
    """Fixtures are hardcoded passing data — test-only, opt-in by env."""
    return os.environ.get("MERGE_THROUGHPUT_ALLOW_FIXTURE") == "1"


def _read_json_file(path: str):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return UNKNOWN
    return parse_api_body(data)


def _payload_from_input(data, name: str):
    """A whole record, or a check-name -> record map.

    A whole record that already carries the check's own top-level key is used
    as-is (e.g. `gap` reads `payload["gap"]`), so `check gap --input <record>`
    is reachable; a per-check map otherwise unwraps to its entry.
    """
    if not isinstance(data, dict):
        return data
    if name == "gap":
        # A gap record is itself `{"gap": {"value", "terms"}}`; only a
        # check-map entry that wraps a whole gap record is unwrapped.
        inner = data.get("gap")
        if isinstance(inner, dict) and "gap" in inner:
            return inner
        return data
    if (
        name in data
        and isinstance(data[name], dict)
        and not any(key in data for key in ("check_runs", "total_count", "items"))
    ):
        return data[name]
    return data


def _cli_check(argv) -> int:
    parsed, error = _parse_check_argv(argv)
    if error is not None:
        print(USAGE, file=sys.stderr)
        return error
    segments, fixture, input_path = parsed
    if fixture is not None and not _fixture_allowed():
        print("2: --fixture is test-only; set MERGE_THROUGHPUT_ALLOW_FIXTURE=1",
              file=sys.stderr)
        return 2
    input_data = _read_json_file(input_path) if input_path else None
    codes = []
    for name, opts in segments:
        opts = dict(opts)
        opts.pop("fixture", None)
        opts.pop("input", None)
        if fixture is not None:
            payload = _fixture_payload(fixture, name)
        elif input_data is not None:
            payload = _payload_from_input(input_data, name)
        else:
            payload = None
        codes.append(run_check(name, json=payload, **opts))
    return aggregate(codes)


# check name → the top-level report field it names, for `--json <name>`.
# Only fields build_report actually emits are listed; an unmapped check name
# exits 2 rather than printing UNKNOWN indistinguishably from a real read.
_CHECK_FIELD = {
    "main-gate": "main_gate",
    "batch-size": "max_batch_size",
    "shard-balance": "shard_imbalance_minutes",
    "fast-files-unclassified": "fast_files_unclassified",
    "conflicts": "conflicts",
    "capacity": "capacity",
    "gap": "gap",
    "durations-map": "durations_map",
}


def _cli_json(argv) -> int:
    fixture = None
    input_path = None
    path = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--fixture" and i + 1 < len(argv):
            fixture = argv[i + 1]
            i += 2
            continue
        if token == "--input" and i + 1 < len(argv):
            input_path = argv[i + 1]
            i += 2
            continue
        if not token.startswith("--"):
            path = token
            i += 1
            continue
        i += 1
    if fixture is not None and not _fixture_allowed():
        print("2: --fixture is test-only; set MERGE_THROUGHPUT_ALLOW_FIXTURE=1",
              file=sys.stderr)
        return 2
    if input_path is not None:
        report = _read_json_file(input_path)
        if not isinstance(report, dict):
            print("2: --input is not a parsable JSON record", file=sys.stderr)
            return 2
    else:
        report = build_report(fixture=fixture)
    if path in _CHECK_FIELD:
        path = _CHECK_FIELD[path]
    elif path in CHECK_NAMES:
        print(jsonlib.dumps({"error": f"no emitted field for check {path!r}"},
                            indent=2))
        return 2
    value = _extract_path(report, path)
    if value is _MISSING:
        print(jsonlib.dumps({"error": f"no such field path {path!r}"}, indent=2))
        return 2
    print(jsonlib.dumps(value, indent=2, default=str))
    return 0


def _owning_issue(pr: dict) -> str:
    """The issue this PR closes/serves, from title, branch or body."""
    title = str(pr.get("title") or "")
    branch = str((pr.get("head") or {}).get("ref") or "")
    body = str(pr.get("body") or "")
    match = re.search(r"\(#(\d+)\)", title)
    if not match:
        match = re.search(r"(?:fix|feat|chore|docs)/(\d+)", branch)
    if not match:
        match = re.search(r"(?:Closes|Fixes|Resolves)\s+#(\d+)", body,
                          re.IGNORECASE)
    return match.group(1) if match else UNKNOWN


def _surface_bucket(surface, stale):
    """The rail's refusal class (a)/(b)/(c) as a bucket, or UNKNOWN.

    Both observations are REQUIRED TOGETHER: a GREEN surface with no staleness
    verdict is UNKNOWN, never `eligible` — the whole point of the class is that
    green alone is not sufficient, and an UNOBSERVED surface must not read as
    "nothing to worry about" (the plan's core discipline).
    """
    if surface in (SURFACE_RED, SURFACE_PENDING):
        return "blocked"
    if surface == SURFACE_GREEN:
        if stale is True:
            return "re_measure"
        if stale is False:
            return "eligible"
    return UNKNOWN


def classify_bucket(number, *, draft, conflict, stale=None, surface=None,
                    hard_stop=None, terminal=None, dead_weight=None):
    """FIRST-MATCH-WINS bucket, in BUCKET_ORDER.

    `draft`/`conflict` are tri-state. Only a POSITIVE observation may place a
    row in `draft`/`conflicting`, and an UNKNOWN observation may NOT fall
    through to `eligible` at the end — an unobserved state must never read as
    "nothing to worry about".
    """
    number = _as_int(number)
    if number is None:
        return UNKNOWN
    if number in (HARD_STOP_D12 if hard_stop is None else hard_stop):
        return "hard_stop"
    if number in (TERMINAL_D5 if terminal is None else terminal):
        return "terminal_decision"
    if number in (DEAD_WEIGHT_EVIDENCE if dead_weight is None else dead_weight):
        return "dead_weight"
    if not isinstance(draft, bool):
        return UNKNOWN
    if draft is True:
        return "draft"
    # `conflict` is tri-state by IDENTITY, exactly like `draft`: a `None`, `1`,
    # `"yes"` or `{}` is an UNOBSERVED conflict, and must never fall through
    # to `eligible` (the plan's "empty is UNKNOWN, never 0" rule).
    if conflict is True:
        return "conflicting"
    if conflict is not False:
        return UNKNOWN
    return _surface_bucket(surface, stale)


def build_triage_row(pr, *, conflict=UNKNOWN, conflicted_paths=None,
                     owner=UNKNOWN, owner_evidence=UNKNOWN,
                     owning_issue=UNKNOWN, hard_stop=None, terminal=None,
                     dead_weight=None, surface=None, stale=None,
                     surface_evidence=""):
    """Exactly TRIAGE_SCHEMA_KEYS. A judgement not made is UNKNOWN, not False."""
    number = _as_int(pr.get("number"))
    draft = pr.get("draft")
    draft_tri = draft if isinstance(draft, bool) else UNKNOWN
    if owning_issue is UNKNOWN:
        owning_issue = _owning_issue(pr)
    hard_set = HARD_STOP_D12 if hard_stop is None else hard_stop
    term_set = TERMINAL_D5 if terminal is None else terminal
    dead_set = DEAD_WEIGHT_EVIDENCE if dead_weight is None else dead_weight
    bucket = classify_bucket(number, draft=draft_tri, conflict=conflict,
                             stale=stale, surface=surface,
                             hard_stop=hard_stop, terminal=terminal,
                             dead_weight=dead_weight)
    # `superseded_by` carries the evidence the BUCKET decision used — a caller
    # that classifies with its own `dead_weight` mapping must not get a
    # dead_weight bucket with the evidence column detached from it.
    superseded = (dead_set.get(number, UNKNOWN)
                  if isinstance(dead_set, dict) else UNKNOWN)
    return {
        "number": number,
        "bucket": bucket,
        "eligible": UNKNOWN if bucket is UNKNOWN else bucket == "eligible",
        "conflict": conflict,
        "conflicted_paths": list(conflicted_paths or []),
        "superseded_by": superseded,
        "surface_evidence": surface_evidence,
        "draft": draft_tri,
        "hard_stop": number in hard_set if number is not None else UNKNOWN,
        "terminal_decision": number in term_set if number is not None else UNKNOWN,
        "owner": owner,
        "owner_evidence": owner_evidence,
        "owning_issue": owning_issue,
    }


def validate_triage_rows(rows, *, total_count=None,
                         min_population=MIN_OPEN_PR_POPULATION,
                         hard_stop=None, terminal=None, dead_weight=None):
    """[] when the Task-5 acceptance holds; otherwise the list of violations.

    Asserts: the EXACT schema keys per row; `bucket` is the FIRST match of the
    row's own independently-reported columns; `eligible` agrees with `bucket`;
    the boolean columns are booleans; `owner` is never the shared author login;
    and the row count reconciles to the enumerated open-PR total.
    """
    if not isinstance(rows, list):
        return ["rows is not a list"]
    hard_set = HARD_STOP_D12 if hard_stop is None else hard_stop
    term_set = TERMINAL_D5 if terminal is None else terminal
    errors: list = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row {index} is not a mapping")
            continue
        keys = set(row)
        if keys != set(TRIAGE_SCHEMA_KEYS):
            errors.append(
                f"row {row.get('number')}: schema keys differ "
                f"(missing={sorted(set(TRIAGE_SCHEMA_KEYS) - keys)}, "
                f"extra={sorted(keys - set(TRIAGE_SCHEMA_KEYS))})"
            )
            continue
        # UNKNOWN is NOT a bucket: a row whose dimension could not be observed
        # is a NON-CLEAN read, and must fail the validator rather than pass as
        # a row with an unrecognised label.
        if row["bucket"] not in BUCKET_ORDER:
            errors.append(
                f"row {row['number']}: bucket {row['bucket']!r} is not one of "
                f"the buckets (an unobserved row is non-clean)"
            )
        # The surface dimension travels ON the row as `surface_evidence`, and
        # the bucket is re-derived from what that evidence SAYS — so a bucket
        # that disagrees with its own evidence is caught here, not trusted.
        evidence_text = row["surface_evidence"]
        if not isinstance(evidence_text, str):
            errors.append(
                f"row {row['number']}: surface_evidence is not a string")
        evidence_surface, evidence_stale = surface_from_evidence(evidence_text)
        if evidence_surface is UNKNOWN:
            errors.append(
                f"row {row['number']}: surface_evidence is not a well-formed "
                f"observation ({evidence_text!r})"
            )
        expected = classify_bucket(
            row["number"], draft=row["draft"], conflict=row["conflict"],
            surface=evidence_surface, stale=evidence_stale,
            hard_stop=hard_stop, terminal=terminal, dead_weight=dead_weight,
        )
        if expected != row["bucket"]:
            errors.append(
                f"row {row['number']}: bucket {row['bucket']!r} is not the "
                f"first match ({expected!r})"
            )
        # `eligible` is a strict boolean once the bucket is decided.
        if row["bucket"] != UNKNOWN and row["eligible"] is not (
                row["bucket"] == "eligible"):
            errors.append(f"row {row['number']}: eligible disagrees with bucket")
        # The boolean columns are recorded INDEPENDENTLY of `bucket` — so they
        # must be asserted against their own source, not merely type-checked.
        for key in ("draft", "hard_stop", "terminal_decision"):
            if not isinstance(row[key], bool):
                errors.append(f"row {row['number']}: {key} is not boolean")
        number = _as_int(row["number"])
        if isinstance(row["hard_stop"], bool) and number is not None and \
                row["hard_stop"] != (number in hard_set):
            errors.append(
                f"row {row['number']}: hard_stop disagrees with the D12 set")
        if isinstance(row["terminal_decision"], bool) and number is not None and \
                row["terminal_decision"] != (number in term_set):
            errors.append(
                f"row {row['number']}: terminal_decision disagrees with the D5 set")
        # A dead_weight row needs POSITIVE evidence: not merely "not the
        # sentinel" — `""`, `"null"`, `None` and `0` all read as absent to
        # S15's own exclusion test, so they must not validate here either.
        if row["bucket"] == "dead_weight" and not (
                isinstance(row["superseded_by"], str)
                and row["superseded_by"].strip()
                and row["superseded_by"] not in ("null", UNKNOWN)):
            errors.append(
                f"row {row['number']}: dead_weight carries no superseded_by evidence")
        # When the default evidence map names this PR, the row's evidence must
        # BE that verified claim — a fabricated `superseded_by` on a real
        # dead_weight PR is the same defect as no evidence at all.
        number = _as_int(row.get("number"))
        if (row["bucket"] == "dead_weight" and number in DEAD_WEIGHT_EVIDENCE
                and row["superseded_by"] != DEAD_WEIGHT_EVIDENCE[number]):
            errors.append(
                f"row {row['number']}: superseded_by does not match the verified "
                f"evidence for this PR")
        # `superseded_by` is INDEPENDENT evidence, but only a bucket that
        # PRECEDES dead_weight may carry it — otherwise the row's first match
        # should have been dead_weight (a marker on draft/conflicting/eligible
        # is inconsistent, and S15 refuses it too).
        rank = {name: index for index, name in enumerate(BUCKET_ORDER)}
        if (isinstance(row["superseded_by"], str)
                and row["superseded_by"] not in (UNKNOWN, "", "null")
                and row["bucket"] in rank
                and rank[row["bucket"]] > rank["dead_weight"]):
            errors.append(
                f"row {row['number']}: superseded_by is set on a "
                f"{row['bucket']!r} row (its first match should be dead_weight)")
        if not isinstance(row["conflict"], bool) and row["conflict"] != UNKNOWN:
            errors.append(f"row {row['number']}: conflict is not tri-state")
        paths = row["conflicted_paths"]
        if not isinstance(paths, list) or any(
                not isinstance(p, str) or not p.strip() for p in paths):
            errors.append(
                f"row {row['number']}: conflicted_paths is not a list of non-empty strings")
        elif row["conflict"] is True and not paths:
            errors.append(
                f"row {row['number']}: a conflict carries no conflicted_paths")
        elif row["conflict"] is False and paths:
            errors.append(
                f"row {row['number']}: a clean merge carries conflicted_paths")
        owner = row["owner"]
        if owner != UNKNOWN and not (isinstance(owner, str) and owner.strip()):
            errors.append(f"row {row['number']}: owner is neither UNKNOWN nor a label")
        if isinstance(owner, str) and owner == FLEET_AUTHOR_LOGIN:
            errors.append(
                f"row {row['number']}: owner is the shared PR-author login"
            )
        # A `session:` owner must BE the session cited in its own evidence — a
        # bare substring test is satisfied by `session:branch` or `session:`.
        if (isinstance(owner, str) and owner.startswith("session:")
                and isinstance(row["owner_evidence"], str)):
            fragment = owner[len("session:"):]
            cited = re.search(r"session=([0-9a-fA-F-]+)", row["owner_evidence"])
            if (len(fragment) < 4 or not cited
                    or not cited.group(1).startswith(fragment)):
                errors.append(
                    f"row {row['number']}: owner is not the session cited in "
                    f"owner_evidence")
        evidence = row["owner_evidence"]
        if owner != UNKNOWN:
            if not (isinstance(evidence, str) and evidence.strip()):
                errors.append(
                    f"row {row['number']}: a resolved owner carries no owner_evidence")
            else:
                # The plan requires the TRIPLE, each part present AND non-empty.
                for field in ("branch=", "session=", "first_msg="):
                    index = evidence.find(field)
                    value = ("" if index < 0
                             else evidence[index + len(field):].split(";", 1)[0])
                    if not value.strip():
                        errors.append(
                            f"row {row['number']}: owner_evidence is missing "
                            f"{field!r} (session + branch + first message is the "
                            f"required triple)")
                        break
    if total_count is not None:
        numbers = [row.get("number") for row in rows if isinstance(row, dict)]
        if len(numbers) != len(set(numbers)):
            errors.append("row numbers are not unique (one row per open PR)")
        if len(rows) != total_count:
            errors.append(
                f"rows={len(rows)} does not reconcile to total_count={total_count}"
            )
        if _is_num(total_count) and total_count < min_population:
            errors.append(
                f"population {total_count} is below the floor {min_population}"
            )
    return errors


# ---------------------------------------------------------------------------
# Task 5 — the evaluated-tree surface (the rail's §4.5/§4.6/§4.7).
#
# THE RAIL IS AUTHORITATIVE for these judgements (plan I6). `admin-merge.sh`
# reads the PR's evaluated-tree surface, refuses at §4.5 when that surface is
# red, and then refuses at §4.6/§4.7 when the surface is GREEN but a base red
# began after it was produced (a STALE GREEN), or when the merge ref's base
# parent lags a red base. These helpers OBSERVE that rule; they never override
# the rail, and the token sets above are pinned against it by a parity test so
# the two cannot drift.
# ---------------------------------------------------------------------------

def classify_surface_runs(runs):
    """(verdict, anchor_iso, reds, pending) for one check-run surface.

    Mirrors the rail's check-surface probe (#1261), which is the AUTHORITY for
    the RE-MEASURE class:

    * the newest attempt per `(app.slug, name)` decides — a re-run leaves the
      OLD failing run in place beside the new one, so an ungrouped read reports
      a red GitHub itself shows green;
    * an UNRESOLVED identity (an unorderable multi-attempt group) is UNKNOWN,
      never GREEN — the fail-closed direction the rail takes for a surface it
      cannot read;
    * a COMPLETED run is non-red only for a conclusion in
      `NON_RED_CONCLUSIONS`; every OTHER spelling, including a null one, is RED;
    * an in-flight status is PENDING, never red;
    * only a MEASURING conclusion may set the surface's last-production time,
      and a run that exercised nothing must not advance it.

    Grouping deliberately mirrors the rail's `(app, name)` rather than the
    instrument's finer `(app, workflow, name)`: the rail DEFINES this refusal
    class, and the coarser grouping can only offer a stale surface MORE often,
    which is the safe direction for a re-measure request.
    """
    if not isinstance(runs, list):
        return UNKNOWN, None, [], 0
    groups: dict = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        name = str(run.get("name") or "") or UNNAMED_CHECK
        groups.setdefault((_app_slug(run), name), []).append(run)
    if not groups:
        return UNKNOWN, None, [], 0
    reds = []
    pending = 0
    anchor = None
    for key, group in groups.items():
        newest = _newest(group)
        if newest is None:
            return UNKNOWN, None, [], 0
        status = str(newest.get("status") or "")
        concl = str(newest.get("conclusion") or "")
        if status != "completed":
            if status in IN_FLIGHT_STATUSES:
                pending += 1
            elif concl not in NON_RED_CONCLUSIONS:
                reds.append((key[1], str(newest.get("started_at") or "")))
            continue
        completed = str(newest.get("completed_at") or "")
        parsed = _parse_ts(completed)
        if concl in MEASURING_CONCLUSIONS and parsed is not None:
            if anchor is None or parsed > anchor[0]:
                anchor = (parsed, completed)
        if concl not in NON_RED_CONCLUSIONS:
            reds.append((key[1], str(newest.get("started_at") or "")))
    if reds:
        verdict = SURFACE_RED
    elif pending:
        verdict = SURFACE_PENDING
    else:
        verdict = SURFACE_GREEN
    return verdict, (anchor[1] if anchor else None), reds, pending


def surface_probe(sha: str):
    """`classify_surface_runs` over the check runs attached to `sha`."""
    runs, _total = fetch_check_runs(sha)
    if runs is UNKNOWN:
        return UNKNOWN, None, [], 0
    return classify_surface_runs(runs)


def base_blocking_reds():
    """(main_sha, [(job, started_iso)]) — §4.6's clock, or UNKNOWN.

    An EMPTY red list is a POSITIVE observation: §4.6 is RED-RELATIVE, so a
    green base has nothing a stale surface could have failed to measure (the
    rail refuses only on a red, because refusing on movement alone would refuse
    essentially every open PR). UNKNOWN means the read failed and no staleness
    judgement is possible.
    """
    body = _gh_api(f"repos/{OWNER_REPO}/commits/main")
    if body is UNKNOWN or not isinstance(body, dict):
        return UNKNOWN
    sha = body.get("sha")
    if not _is_sha(sha):
        return UNKNOWN
    verdict, _anchor, reds, _pending = surface_probe(sha)
    if verdict is UNKNOWN:
        return UNKNOWN
    return sha, reds


def merge_ref_base_parent(pr: dict):
    """The base commit `refs/pull/<N>/merge` was computed against, or UNKNOWN.

    §4.7's second, independent signal: the merge ref is a merge commit whose
    FIRST parent is the base it evaluated against. `merge_commit_sha` is NULL
    for a conflicted PR (there is no computed tree), which is UNKNOWN here —
    the `conflicting` bucket already owns that row.
    """
    merge_sha = pr.get("merge_commit_sha")
    if not _is_sha(merge_sha):
        return UNKNOWN
    body = _gh_api(f"repos/{OWNER_REPO}/commits/{merge_sha}")
    if body is UNKNOWN or not isinstance(body, dict):
        return UNKNOWN
    parents = body.get("parents")
    if not isinstance(parents, list) or not parents:
        return UNKNOWN
    first = parents[0]
    if not isinstance(first, dict) or not _is_sha(first.get("sha")):
        return UNKNOWN
    return first["sha"]


def _stale_by_clock(anchor_iso, clock):
    """(stale, red_job, red_started) for a GREEN surface against base reds.

    `clock` is `[(parsed_start, job, started_iso)]`. §4.6 refuses when ANY
    base red's run STARTED after the surface was last produced; an UNREADABLE
    base-red start is a red the rail cannot show the surface measured, so it
    refuses too (the unreadable-time arm), never passes. An EMPTY surface
    anchor (no MEASURING completed check) is §4.6's fail-closed arm.
    """
    if not clock:
        return False, None, None
    anchor = _parse_ts(anchor_iso)
    if anchor is None:
        return True, clock[0][1], clock[0][2]
    for parsed, job, started in clock:
        if parsed is None or parsed > anchor:
            return True, job, started
    return False, None, None


def build_surface_evidence(verdict, *, produced=None, stale=None,
                           base_red_started=None, red=None,
                           merge_ref_base=None, base_head=None,
                           reds=(), pending=0):
    """The canonical `surface_evidence` string for one row.

    The FIRST field is always `verdict=<GREEN|RED|PENDING>` so a reader can
    re-derive the surface-conditioned bucket; the rest is the timestamp/parent
    evidence that puts the row in, or out of, `re_measure`/`blocked`. A GREEN
    surface ALWAYS carries a `stale=0|1` verdict — a green with no staleness
    verdict is not a state this producer can emit.
    """
    parts = [f"verdict={verdict}"]
    if verdict == SURFACE_GREEN:
        parts.append(f"stale={1 if stale is True else 0}")
        parts.append(f"produced={produced}" if produced
                     else "no_measuring_check=1")
        if stale is True:
            if base_red_started:
                parts.append(f"base_red_started={base_red_started}")
            if red:
                parts.append(f"red={red}")
            if merge_ref_base:
                parts.append(f"merge_ref_base={merge_ref_base}")
            if base_head:
                parts.append(f"base_head={base_head}")
    else:
        if reds:
            parts.append("reds=" + ",".join(sorted(str(r) for r in reds)))
        if pending:
            parts.append(f"pending={pending}")
    return "; ".join(parts)


def surface_from_evidence(evidence):
    """(verdict, stale) parsed from a row's `surface_evidence`, else UNKNOWN.

    Strict on purpose: a hand-written or truncated evidence string cannot
    validate, and a GREEN with no `stale` field is UNKNOWN — the whole point
    of the class is that green alone is not sufficient.
    """
    if not isinstance(evidence, str) or not evidence.strip():
        return UNKNOWN, UNKNOWN
    fields: dict = {}
    for part in evidence.split(";"):
        if "=" not in part:
            return UNKNOWN, UNKNOWN
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or key in fields:
            return UNKNOWN, UNKNOWN
        fields[key] = value
    verdict = fields.get("verdict")
    if verdict not in _SURFACE_VERDICTS:
        return UNKNOWN, UNKNOWN
    if verdict != SURFACE_GREEN:
        return verdict, False
    stale = fields.get("stale")
    if stale not in ("0", "1"):
        return UNKNOWN, UNKNOWN
    if stale == "0" and not (fields.get("produced")
                             or fields.get("no_measuring_check") == "1"):
        return UNKNOWN, UNKNOWN
    if stale == "1" and not (fields.get("base_red_started")
                             or fields.get("merge_ref_base")
                             or fields.get("no_measuring_check") == "1"):
        return UNKNOWN, UNKNOWN
    return verdict, stale == "1"


def collect_triage_stale_surface(prs, base=None, bound=None):
    """{number: surface_evidence} for every open PR — §4.6 and §4.7.

    Each value is the canonical `surface_evidence` string; `stale` and the
    surface verdict are read back from it by `surface_from_evidence`, so the
    row and its evidence have ONE source of truth. Returns `None` when the
    base or ANY PR surface could not be read: a partial staleness
    classification is exactly the stale-green this bucket exists to catch, so
    the caller refuses rather than emitting a mislabelled row.
    """
    if base is None:
        base = base_blocking_reds()
    if base is UNKNOWN:
        return None
    base_sha, base_reds = base
    if not isinstance(prs, list) or not _is_sha(base_sha):
        return None
    bound = validate_sweep_concurrency(bound)
    clock = [(_parse_ts(started), job, started) for job, started in base_reds]
    candidates = [pr for pr in prs if isinstance(pr, dict)]

    def probe(pr):
        number = _as_int(pr.get("number"))
        head = (pr.get("head") or {}).get("sha")
        if number is None or not _is_sha(head):
            return number, UNKNOWN, None, [], 0, UNKNOWN
        verdict, anchor, reds, pending = surface_probe(head)
        parent = UNKNOWN
        # §4.7 costs a call, so it is probed only when the base is red AND
        # §4.6 did not already refuse — it is the second, independent signal.
        if verdict == SURFACE_GREEN and clock:
            stale_46, _job, _started = _stale_by_clock(anchor, clock)
            if stale_46 is not True:
                parent = merge_ref_base_parent(pr)
        return number, verdict, anchor, reds, pending, parent

    out: dict = {}
    for number, verdict, anchor, reds, pending, parent in bounded_map(
            probe, candidates, bound):
        if number is None or verdict is UNKNOWN:
            return None
        if verdict != SURFACE_GREEN:
            out[number] = build_surface_evidence(
                verdict, reds=[name for name, _started in reds],
                pending=pending)
            continue
        stale, red_job, red_started = _stale_by_clock(anchor, clock)
        if (stale is False and _is_sha(parent) and parent != base_sha):
            # §4.7: the merge ref was computed against a base that does not
            # contain the current base's red — the green was never measured
            # against the tree the merge will produce.
            out[number] = build_surface_evidence(
                verdict, produced=anchor, stale=True,
                merge_ref_base=parent, base_head=base_sha)
            continue
        out[number] = build_surface_evidence(
            verdict, produced=anchor, stale=stale,
            base_red_started=(red_started if stale else None),
            red=(red_job if stale else None))
    return out


def collect_triage_conflicts(prs=None, bound=None):
    """{pr number: (conflict, conflicted_paths)} over every open PR.

    `prs` may be an already-enumerated open-PR list (so the caller sweeps the
    SAME population it will emit rows for). Returns None when the population
    could not be enumerated or `origin/main` moved under the sweep. A PR whose
    head cannot be probed maps to (UNKNOWN, []) — never (False, []).
    """
    if prs is None:
        prs = _gh_api(
            f"repos/{OWNER_REPO}/pulls?state=open&per_page=100&sort=created&direction=asc",
            paginate=True,
        )
    body = prs
    if body is UNKNOWN or not isinstance(body, list):
        return None
    # A sweep that reads `origin/main` moving under it produces a MIXED read —
    # some rows probed against one main, some against another. Task 1's
    # `collect_conflicts` refuses that shape; the triage sweep must not be the
    # one path that accepts it.
    before = live_main_sha()
    if before is UNKNOWN:
        return None
    bound = validate_sweep_concurrency(bound)
    pairs = []
    for pr in body:
        if not isinstance(pr, dict):
            continue
        pairs.append((pr.get("number"), (pr.get("head") or {}).get("sha")))
    probe = [(number, sha) for number, sha in pairs if sha]
    bounded_map(_ensure_object, [sha for _number, sha in probe], bound)
    results = bounded_map(
        lambda item: merge_tree_conflict_detail(REPO, "origin/main", item[1]),
        probe, bound,
    )
    if assert_main_unchanged(before, live_main_sha()) is UNKNOWN:
        print("UNKNOWN: origin/main moved during the triage conflict sweep",
              file=sys.stderr)
        return None
    # An INDEX loop, not `zip(..., strict=)`: the §11 criteria run this tool as
    # `python3`, and on this host that is 3.9.6 — `strict=` is 3.10+ (round 10
    # fixed exactly this class in `collect_conflicts`; the triage sweep must not
    # reintroduce it, because a crash at import/exec exits 1 — the contract's
    # MISS — and would fabricate a verdict).
    out = {}
    for index, (number, _sha) in enumerate(probe):
        out[number] = results[index]
    for number, _sha in pairs:
        out.setdefault(number, (UNKNOWN, []))
    return out


def load_triage_owner_evidence(path=None):
    """{pr number: {owner, owner_evidence}} from the committed evidence file.

    Absent, unreadable, or malformed is an EMPTY map (owners become UNKNOWN) —
    never a fallback to the PR author, which no lane can be derived from.
    """
    target = Path(path) if path else TRIAGE_OWNER_EVIDENCE_PATH
    try:
        payload = jsonlib.loads(target.read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        payload = payload["rows"]
    out = {}
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and _as_int(entry.get("number")) is not None:
                out[_as_int(entry["number"])] = entry
    elif isinstance(payload, dict):
        for key, entry in payload.items():
            number = _as_int(key)
            if number is not None and isinstance(entry, dict):
                out[number] = entry
    return out


def _triage_rows(conflicts=None, owners=None):
    """(rows, enumerated_total) for every open PR, or (None, 0) on a failed read.

    NEVER issues a mutating request. `enumerated_total` is the INDEPENDENT
    open-PR count (the pagination Link header), so the caller reconciles the row
    count against the API, not against `len(rows)` — which can never disagree.
    """
    body = _gh_api(
        f"repos/{OWNER_REPO}/pulls?state=open&per_page=100&sort=created&direction=asc",
        paginate=True,
    )
    if body is UNKNOWN or not isinstance(body, list):
        return None, 0
    # `live` distinguishes a real read from a fixture call: only a real read is
    # reconciled against the API's independent total.
    live = conflicts is None
    if conflicts is None:
        # Sweep the SAME population the rows will be emitted for.
        conflicts = collect_triage_conflicts(body)
    # A FAILED sweep (`None`) is not an empty sweep: refusing it is what keeps
    # an unread conflict dimension from validating clean.
    if conflicts is None:
        return None, 0
    # The surface dimension is swept over the SAME population. A failed read
    # refuses the whole triage: a partial staleness classification is exactly
    # the stale-green this bucket exists to catch.
    surfaces = collect_triage_stale_surface(body)
    if surfaces is None:
        print("UNKNOWN: could not classify the evaluated-tree surfaces",
              file=sys.stderr)
        return None, 0
    # An independent total, when we are the live reader: a partial enumeration
    # that reconciles only against itself is the silent-truncation failure.
    total = len(body)
    if live:
        independent = open_pr_total()
        if independent is UNKNOWN or independent != len(body):
            print(f"UNKNOWN: enumerated {len(body)} open PRs but the API reports "
                  f"{independent}", file=sys.stderr)
            return None, 0
        total = independent
    if owners is None:
        owners = load_triage_owner_evidence()
    rows = []
    for pr in body:
        if not isinstance(pr, dict):
            continue
        number = _as_int(pr.get("number"))
        detail = conflicts.get(number, (UNKNOWN, []))
        conflict, paths = detail if isinstance(detail, tuple) else (detail, [])
        evidence = owners.get(number) or {}
        # A JSON `null` must become UNKNOWN, not a `None` that reads as a label.
        owner = evidence.get("owner") or UNKNOWN
        owner_evidence = evidence.get("owner_evidence") or UNKNOWN
        # The surface verdict and its staleness are read back from the ONE
        # evidence string, so the row cannot disagree with its own evidence.
        surface_evidence = surfaces.get(number, "")
        surface, stale = surface_from_evidence(surface_evidence)
        rows.append(build_triage_row(
            pr,
            conflict=conflict,
            conflicted_paths=paths,
            surface=surface,
            stale=stale,
            surface_evidence=surface_evidence,
            owner=owner,
            owner_evidence=owner_evidence,
        ))
    return rows, total


def _cli_triage(argv) -> int:
    emit_rows = "--emit" in argv and "rows" in argv
    fixture = None
    for i, token in enumerate(argv):
        if token == "--fixture" and i + 1 < len(argv):
            fixture = argv[i + 1]
    if fixture is not None:
        if not _fixture_allowed():
            return 2
        rows = _FIXTURES.get(fixture, {}).get("triage", _FIXTURE_TRIAGE_ROWS)
        total = len(rows)
        # A fixture is synthetic: the open-PR population floor is a LIVE-read
        # guard, so it does not apply to test data.
        errors = validate_triage_rows(rows, total_count=total, min_population=1)
    else:
        rows, total = _triage_rows()
        if rows is None:
            print("2: could not enumerate open PRs", file=sys.stderr)
            return 2
        # Reconcile against the ENUMERATION's own total, not `len(rows)`.
        errors = validate_triage_rows(rows, total_count=total)
    if errors:
        for error in errors:
            print(f"2: triage invariant violated — {error}", file=sys.stderr)
        return 2
    payload = rows if emit_rows else {
        "rows": rows, "total_count": total, "bucket_order": list(BUCKET_ORDER)}
    print(jsonlib.dumps(payload, indent=2, default=str))
    return 0


def _capacity_sample():
    runs = _gh_api(
        f"repos/{OWNER_REPO}/actions/runs?per_page=100", paginate=True
    )
    if runs is UNKNOWN or not isinstance(runs, dict):
        return None
    active = [
        run for run in runs.get("workflow_runs", []) or []
        if isinstance(run, dict) and run.get("status") in ("queued", "in_progress")
    ]
    return {
        "queued": sum(1 for run in active if run.get("status") == "queued"),
        "in_progress": sum(1 for run in active if run.get("status") == "in_progress"),
    }


def _cli_observe(mode: str, argv) -> int:
    fixture = None
    for i, token in enumerate(argv):
        if token == "--fixture" and i + 1 < len(argv):
            fixture = argv[i + 1]
    if fixture is not None:
        if not _fixture_allowed():
            return 2
        print(jsonlib.dumps({"mode": mode.lstrip("-"), "samples": []}, indent=2))
        return 0
    sample = _capacity_sample()
    if sample is None:
        print(jsonlib.dumps({"mode": mode.lstrip("-"), "samples": [],
                             "status": UNKNOWN}, indent=2))
        return 2
    print(jsonlib.dumps({"mode": mode.lstrip("-"), "samples": [sample]}, indent=2))
    return 0


def _cli_sweep(argv) -> int:
    bound = validate_sweep_concurrency(argv[0] if argv else None)
    body = _gh_api(
        f"repos/{OWNER_REPO}/pulls?state=open&per_page=100", paginate=True
    )
    if body is UNKNOWN or not isinstance(body, list):
        print("2: could not enumerate open PRs", file=sys.stderr)
        return 2
    refs = [
        p["head"]["ref"] for p in body
        if isinstance(p, dict) and p.get("head", {}).get("ref")
    ]

    def probe(ref):
        return merge_tree_conflict(REPO, "origin/main", f"origin/{ref}")

    results = bounded_map(probe, refs, bound)
    conflicting = sum(1 for r in results if r is True)
    unknown = sum(1 for r in results if r is UNKNOWN)
    print(jsonlib.dumps({"probed": len(refs), "conflicting": conflicting,
                         "unknown": unknown, "bound": bound}, indent=2))
    return 0 if unknown == 0 else 2


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    mode = args[0]
    rest = args[1:]
    if mode == "check":
        return _cli_check(rest)
    if mode == "--json":
        return _cli_json(rest)
    if mode == "--triage":
        return _cli_triage(rest)
    if mode in ("--watch-queue", "--observe-capacity"):
        return _cli_observe(mode, rest)
    if mode == "--sweep-concurrency":
        return _cli_sweep(rest)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
