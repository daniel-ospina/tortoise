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

Stdlib only (Python 3.12).
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
from datetime import UTC, datetime
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
# S13: `.gap.value` is independently measured as ceiling / observed, so the
# reconciliation can fail. Tolerance stated numerically (±10%).
GAP_VALUE_TOLERANCE = 0.10
CEILING_MAX = 200000
CEILING_TOLERANCE = 0.10  # ±10%, stated numerically

EXCLUDE_KEYS = frozenset({"hard_stop", "terminal_decision", "draft", "superseded_by"})

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
        dt = dt.replace(tzinfo=UTC)
    return dt


def _age_days(value: object):
    dt = _parse_ts(value)
    if dt is None:
        return None
    return (datetime.now(UTC) - dt).total_seconds() / 86400.0


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


def _newest(group: list[dict]):
    """Newest attempt by `id`, or None when the group cannot be totally ordered.

    A partial order is not an order: if any attempt in a multi-attempt group
    lacks an integer `id`, list order would decide the newest and an older red
    could be shadowed by a newer green. That is UNKNOWN, never GREEN.
    """
    if len(group) == 1:
        return group[0]
    if any(_run_id(run) is None for run in group):
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


def merge_tree_conflict(repo, main_ref: str, branch_ref: str):
    """git merge-tree conflict probe. True/False, or UNKNOWN.

    A deleted branch or an unresolvable ref is UNKNOWN — never "no conflict".
    """
    for ref in (main_ref, branch_ref):
        try:
            exists = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", ref],
                cwd=str(repo), capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return UNKNOWN
        if exists.returncode != 0:
            return UNKNOWN
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
        return UNKNOWN
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    return UNKNOWN


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
        if isinstance(p, dict) and p.get("head", {}).get("sha")
    ]
    bounded_map(lambda item: _ensure_object(item[1]), refs, bound)

    def probe(item):
        return merge_tree_conflict(REPO, "origin/main", item[1])

    results = bounded_map(probe, refs, bound)
    after = live_main_sha()
    if assert_main_unchanged(main_sha, after) is UNKNOWN:
        return {"items": [], "total_count": 0, "read_ok": True,
                "main_moved": True}
    items = []
    for (pr, _sha), conflict in zip(refs, results, strict=False):
        items.append({
            "number": pr.get("number"),
            "branch": pr.get("head", {}).get("ref"),
            "conflicting": conflict is True,
            "unknown": conflict is UNKNOWN,
        })
    return {"items": items, "total_count": len(items), "read_ok": True}


def collect_fast_files_unclassified():
    """Files in no manifest classification. Consumes `fast_pool()` — the

    single source of truth for the fast subset — so the exclusion set cannot
    silently disagree with the halves' definition (#5215 cycle 4).
    """
    try:
        sys.path.insert(0, str(REPO / "tools"))
        import ci_selection as cs

        manifest = cs.load_manifest()
        on_disk = {p.name for p in (REPO / "tests").rglob("test_*.py")}
        fast = set(cs.fast_pool(manifest))
        deliberate = (
            set(manifest.get("slow_files", []))
            | cs.carve_out_files(manifest)
            | set(cs.ENV_BROKEN_FILES)
        )
        return {"fast_files_unclassified": sorted(on_disk - (fast | deliberate))}
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
        if not record_sha or not live_sha or record_sha != live_sha:
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
    if measured is None:
        print("2: prs_per_day is UNKNOWN (a ceiling artifact does not measure it)")
        return 2
    if spec is not None and not spec:
        print("2: --or-artifact requires a PATH#ANCHOR value")
        return 2
    if spec:
        code, ceiling = _evaluate_ceiling(spec, payload)
        if code is not None:
            return code
        if measured > ceiling:
            print(f"1: measured {measured}/day exceeds the documented ceiling {ceiling}")
            return 1
        return 0
    if measured is None:
        return 2
    threshold = _as_number(opts.get("min"))
    if threshold is None:
        return 2
    return 0 if measured >= threshold else 1


_CEILING_RE = re.compile(r"^\s*ceiling_prs_per_day\s*:\s*(.*?)\s*$", re.M)
_SOURCE_RE = re.compile(r"^\s*ceiling_source\s*:\s*(.*?)\s*$", re.M)


def _evaluate_ceiling(spec: str, payload: dict):
    """Typed INTEGER ceiling from an anchored section. `(None, value)` on ok."""
    if "#" not in spec:
        return 2, None
    path_part, anchor = spec.rsplit("#", 1)
    path = Path(path_part)
    if not path.is_absolute():
        path = REPO / path
    try:
        text = path.read_text()
    except OSError:
        print(f"2: artifact unreadable: {path}")
        return 2, None
    section = _section(text, anchor)
    if section is None:
        print(f"2: anchor #{anchor} not found in {path}")
        return 2, None
    ceiling_matches = _CEILING_RE.findall(section)
    if len(ceiling_matches) != 1:
        print(f"2: ceiling_prs_per_day must appear exactly once ({len(ceiling_matches)})")
        return 2, None
    raw = ceiling_matches[0]
    if not re.fullmatch(r"\d+", raw):
        print(f"2: ceiling_prs_per_day must be a typed integer, got {raw!r}")
        return 2, None
    ceiling = int(raw)
    if not (0 < ceiling <= CEILING_MAX):
        return 2, None
    source_matches = _SOURCE_RE.findall(section)
    if len(source_matches) != 1 or not source_matches[0]:
        print("2: ceiling_source must name the .gap.terms keys")
        return 2, None
    sources = [s.strip() for s in source_matches[0].split(",") if s.strip()]
    emitted = _emitted_terms(payload)
    if emitted is None:
        print("2: --or-artifact requires the emitted .gap.terms "
              "(a self-set integer is not a derived ceiling)")
        return 2, None
    values = {}
    for key in sources:
        if key not in emitted or not _is_num(emitted[key]):
            print(f"2: ceiling_source names {key!r} absent from .gap.terms")
            return 2, None
        values[key] = emitted[key]
    for needed in ("effective_parallel", "effective_batch", "cycle_minutes"):
        if needed not in values:
            print(f"2: ceiling is not derivable — missing {needed!r}")
            return 2, None
    if not _is_num(values.get("effective_parallel")) or values["effective_parallel"] <= 0:
        print("2: effective_parallel must be a positive number in .gap.terms")
        return 2, None
    if not _is_num(values.get("effective_batch")) or values["effective_batch"] <= 0:
        print("2: effective_batch must be a positive number in .gap.terms")
        return 2, None
    cycle = values["cycle_minutes"]
    if not _is_num(cycle) or cycle <= 0:
        print("2: cycle_minutes must be a positive number in MINUTES")
        return 2, None
    derived = round(values["effective_parallel"] * values["effective_batch"] * 1440 / cycle)
    tolerance = max(1, round(derived * CEILING_TOLERANCE))
    if abs(ceiling - derived) > tolerance:
        print(f"2: ceiling {ceiling} does not reconcile with derived {derived} (±10%)")
        return 2, None
    return None, ceiling


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
    if entered is not True:
        print(f"2: entered_queue is {entered!r}, not a boolean true")
        return 2
    if trigger != "auto_merge_conditions":
        print(f"1: queue entry was not unaided (trigger={trigger!r})")
        return 1
    if opts.get("require_fresh"):
        head = payload.get("head_sha")
        run_sha = payload.get("run_sha")
        if not head or not run_sha or head != run_sha:
            print("2: queue-entry evidence is not on the PR's current head")
            return 2
        if not _age_ok(payload.get("verified_at"), DEFAULT_RECORD_WINDOW_DAYS):
            return 2
    return 0


def _check_queue_eta(payload: dict, opts: dict) -> int:
    if not payload.get("read_ok", True) or payload.get("incomplete_results"):
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
    if not payload.get("read_ok", True) or payload.get("incomplete_results"):
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
    if opts.get("require_fresh") and not _age_ok(
        payload.get("verified_at"), M4_RECORD_WINDOW_DAYS
    ):
        return 2
    cap = _as_number(payload.get("capacity_at_first_failure"))
    configured = _as_number(payload.get("configured_max_parallel_checks"))
    if cap is None or configured is None:
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
    for key, term in terms.items():
        if not isinstance(term, dict):
            return 2
        if not _is_num(term.get("value")):
            print(f"2: gap term {key!r} is not numeric")
            return 2
        if not term.get("source"):
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
    if observed_term["value"] <= 0:
        print("2: .gap.terms.observed must be positive")
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
    if not payload.get("read_ok", True) or payload.get("incomplete_results"):
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
    for row in items:
        for key in ("hard_stop", "terminal_decision", "draft"):
            if key in row and not isinstance(row[key], bool):
                print(f"2: no-languish row {key!r} is not boolean")
                return 2
        superseded = row.get("superseded_by")
        if superseded is not None and not isinstance(superseded, str):
            print("2: no-languish superseded_by is mis-shaped")
            return 2
    excluded = set(excludes)

    def _excluded(row: dict) -> bool:
        label = str(row.get("classification", ""))
        for key in excluded:
            if key == "superseded_by":
                value = row.get("superseded_by")
                if value not in (None, "", "null"):
                    return True
            elif row.get(key) or label == key:
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
    if not head:
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
    if not payload.get("read_ok", True):
        print("2: jobs read was empty/0-byte")
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
    if age > window:
        print(f"1: durations map age {age}d > {window}d")
        return 1
    if payload.get("diverged"):
        print(f"1: durations keys diverged: {payload['diverged']}")
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
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    "bucket": UNKNOWN,
    "eligible": UNKNOWN,
    "conflict": UNKNOWN,
    "conflicted_paths": [],
    "superseded_by": UNKNOWN,
    "draft": False,
    "hard_stop": UNKNOWN,
    "terminal_decision": UNKNOWN,
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
        "fast_files_unclassified": ff.get("fast_files_unclassified", []),
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
            "sampled_keys": dm.get("sampled_keys", 0),
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
    if isinstance(data, dict) and isinstance(data.get(name), dict):
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


def _triage_rows():
    """One Task-5-shaped row per open PR. NEVER mutates anything."""
    body = _gh_api(
        f"repos/{OWNER_REPO}/pulls?state=open&per_page=100&sort=created&direction=asc",
        paginate=True,
    )
    if body is UNKNOWN or not isinstance(body, list):
        return None
    rows = []
    for pr in body:
        if not isinstance(pr, dict):
            continue
        rows.append({
            "number": pr.get("number"),
            # Task 5 owns the bucket taxonomy (hard_stop/terminal_decision/
            # dead_weight/draft/conflicting/eligible). Task 1 emits only what it
            # can observe; a Task-5-owned judgement is UNKNOWN, never a
            # hardcoded False that reads as a real "no".
            "bucket": "draft" if pr.get("draft") else UNKNOWN,
            "eligible": UNKNOWN,
            "conflict": UNKNOWN,
            "conflicted_paths": [],
            "superseded_by": UNKNOWN,
            "draft": bool(pr.get("draft")),
            "hard_stop": UNKNOWN,
            "terminal_decision": UNKNOWN,
            "owner": UNKNOWN,
            "owner_evidence": UNKNOWN,
            "owning_issue": UNKNOWN,
        })
    return rows


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
    else:
        rows = _triage_rows()
        if rows is None:
            print("2: could not enumerate open PRs", file=sys.stderr)
            return 2
    print(jsonlib.dumps(rows if emit_rows else {"rows": rows}, indent=2, default=str))
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
