#!/usr/bin/env python3
"""Queue conflict census (#6138) — the CONFLICTING share of the open PR queue.

The issue's target is a **measurement, not a reduction**: at least 4 consecutive
days of the conflicting share, plus a named top-N of conflict-generating files.
This tool produces that measurement reproducibly, from the API, with no
hand-counting. Its output is the dated artifact
`docs/ci/queue-conflict-census-<YYYY-MM-DD>.json`; the daily series is appended,
never re-measured by hand.

THE FALSE-CONFIDENCE TRAP THIS TOOL EXISTS TO AVOID
--------------------------------------------------
`GET /repos/{owner}/{repo}/pulls` (the LIST endpoint) returns
`"mergeable": null` for **every** open PR — measured on 2026-09-28: 135/135
null. A census that reads the list endpoint and folds `null` into either bucket
reports `CONFLICTING: 0` (or `135`) and is wrong in a way that looks like a
clean read. `mergeable` is a THREE-state field whose third state means "not yet
computed"; only the single-PR GET forces computation. So:

  * the headline share is read from the **per-PR GET**, and
  * `null` is its own bucket (`unknown`) — never folded into `mergeable`, never
    dropped from the population, and the count of unresolved nulls is part of
    the result, and
  * the list endpoint's own distribution is recorded as a diagnostic
    (`list_endpoint_mergeable`), so the trap is visible in every artifact.

Conflicts are ALSO probed with `git merge-tree` (no checkout), because the
GitHub field is known to under-report (the sibling instrument measured 36 vs a
true 44). The merge-tree probe is what yields the **conflicted path set** — the
issue's leg (b) — and the API-vs-merge-tree delta is reported, not hidden.

WHAT EACH LEG REPORTS
---------------------
(a) share    — `{mergeable, conflicting, unknown}`, `share_of_known`,
               `share_of_population`, the snapshot time and the resolved
               `origin/main` SHA.
(b) top-N    — conflicted paths aggregated across every CONFLICTING PR, each
               classified **generated artifact vs hand-written** by a stated,
               mechanical rule (see `classify_path`).
(c) age      — the API does not expose "conflicted since"; the tool reports an
               **upper bound** on conflict age (see `conflict_age_bounds`) plus
               `idle_days` from `updated_at`, and splits abandoned from
               in-progress by a stated rule.

EXIT CONTRACT
-------------
    0  complete census — population enumerated to completeness, every PR's
       mergeable read, `origin/main` stable across the sweep
    2  UNKNOWN — a blocking surface failed (pagination incomplete, a mergeable
       read failed, `origin/main` moved mid-sweep, or `--sample` was used).
       A partial read is never 0.
Unresolved **merge-tree** probes are recorded in `unresolved_prs` and do NOT by
themselves force 2: they are a stated limitation of the path aggregation, and
the share (which needs no merge-tree) is still exact. That distinction is
deliberate — see the docstring of `summarize_share`.

USAGE
    python3 tools/queue_conflict_census.py                 # census + artifact
    python3 tools/queue_conflict_census.py --stdout        # census, JSON to stdout
    python3 tools/queue_conflict_census.py --no-sweep      # API-only (no merge-tree)
    python3 tools/queue_conflict_census.py --fixture F.json  # offline, hermetic

Stdlib only. Newer interpreter features are avoided deliberately: this is run
as a plain `python3` tool, and a crash at import would be an UNKNOWN reported as
a wrong number.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import json
import re
import statistics
import subprocess
import sys
import time

# `timezone.utc`, not `datetime.UTC` (3.11+): see the module docstring's
# interpreter note. `datetime.now(timezone.utc)` is the one clock read.
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OWNER_REPO = "daniel-ospina/tortoise"
SCHEMA = "queue-conflict-census/1"
UNKNOWN = "UNKNOWN"
PER_PAGE = 100
CENSUS_DIR = REPO / "docs" / "ci"

#: The three buckets of the API's `mergeable` field. `unknown` is a FIRST-CLASS
#: bucket, not an error: it is the field's documented "not yet computed" state.
BUCKETS = ("mergeable", "conflicting", "unknown")

#: `git merge-tree --write-tree` is git >= 2.38. Below that the probe is
#: UNKNOWN, never "no conflict".
MIN_MERGE_TREE = (2, 38)
#: This fleet's checkouts are SHALLOW, so a fetched PR head can have no locally
#: reachable merge-base (`git merge-tree` then fails with "refusing to merge
#: unrelated histories"). On exactly that failure the probe is retried ONCE
#: after deepening the PR ref by this many commits. Measured: `origin/main`
#: carries ~5,000 commits and a branch forks a few hundred back, so 300 resolves
#: it; the bound keeps an unbounded history fetch off the table.
DEEPEN_DEPTH = 300

# --------------------------------------------------------------------------
# (b) generated-artifact classifier — the RULE, stated once, here.
# --------------------------------------------------------------------------
#: A path is GENERATED iff either signal fires:
#:   (1) BANNER — the file's first `BANNER_LINES` lines (read from
#:       `origin/main`, not the working tree) carry a generated-artifact banner.
#:       The banner must NAME the file as generated: either the word
#:       `generated` on the same line as an edit prohibition
#:       (`**GENERATED — do not edit.**`, `Generated from X. Do not edit this
#:       file`), or one of `@generated`, `this file is generated`,
#:       `code generated`, `auto-generated`, `generated by`. A bare `do not
#:       edit` is NOT enough — measured false positive:
#:       `benchmarks/bench_core.py` says "do NOT edit without a scoping
#:       revision", and a bare-substring rule labelled hand-written core source
#:       as a generated artifact, which would have aimed the structural remedy
#:       ("stop tracking it") at a hand-written file.
#:   (2) DECLARED OUTPUT — a tracked script under `tools/`, `scripts/` or
#:       `graph-scripts/` WRITES this path. "Writes" is resolved from the AST
#:       around the write call, not from prose: a path merely *mentioned* by a
#:       writing script (e.g. `tortoise/sdk.py` in a shared-module list) is NOT
#:       generated. The evidence names the script, so the classification can be
#:       audited rather than trusted.
#: Otherwise the path is HAND-WRITTEN. The known blind spot is stated, not
#: hidden: a file generated by a tool OUTSIDE this repo (`uv.lock` by `uv`)
#: with no banner and no in-repo generator is classified hand-written, and the
#: artifact's `classifier.blind_spot` says so.
BANNER_RE = re.compile(
    r"(?i)(?:"
    r"generated(?=[^\n]*?(?:do not edit|don't edit|not edit this|do not modify))"
    r"|@generated\b"
    r"|this file is generated\b"
    r"|code generated\b"
    r"|auto-generated\b"
    r"|generated by\b"
    r")"
)
BANNER_LINES = 12
GENERATOR_DIRS = ("tools", "scripts", "graph-scripts")
#: Calls that produce a tracked artifact. A script that never calls one cannot
#: be the producer.
WRITE_METHODS = frozenset({"write_text", "write_bytes", "write", "writelines"})
#: How far `_string_literals` follows a `Name` back to the expression that
#: bound it (`MANIFEST = ROOT / "config" / "ci-surfaces.yml"`). Bounded so a
#: self-referential binding cannot recurse forever.
ALIAS_DEPTH = 5

CLASSIFIER_BLIND_SPOT = (
    "A file produced by a generator outside this repo (e.g. uv.lock by uv) that "
    "carries no banner and is named by no in-repo script is classified "
    "hand-written. The classification is evidence, not proof."
)

TOP_N_DEFAULT = 20
ABANDONED_IDLE_DAYS_DEFAULT = 14
SENSITIVITY_IDLE_DAYS = (3, 7, 14, 30)
SWEEP_CONCURRENCY_DEFAULT = 4
GH_ATTEMPTS = 3
FETCH_TIMEOUT = 240
GIT_TIMEOUT = 180


# ==========================================================================
# Pure logic — the part the tests drive with fixtures and no network.
# ==========================================================================

def classify_mergeable(value) -> str:
    """The API's `mergeable` tri-state -> one of `BUCKETS`, by IDENTITY.

    `True` -> mergeable, `False` -> conflicting, **everything else** -> unknown.
    Identity, not truthiness: `1`, `"true"`, `"True"`, `{}`, `[]` and a missing
    key are all UNKNOWN, never mergeable. Folding `None` into either resolved
    bucket is the false-confidence failure this census exists to prevent.
    """
    if value is True:
        return "mergeable"
    if value is False:
        return "conflicting"
    return "unknown"


def summarize_share(records) -> dict:
    """Headline counts and shares over the whole open population.

    `records` is an iterable of dicts carrying `mergeable_bucket`. Every record
    is counted in exactly one bucket (`BUCKETS`), so the buckets reconcile to
    the population by construction — a `sum(BUCKETS) != population` is a
    classification bug, not a data problem.

    `share_of_known` is the headline: conflicting / (mergeable + conflicting).
    It is `None` when there are no known-state PRs — a 0/0 is UNKNOWN, and
    returning 0.0 there is the divide-by-zero's false-confidence twin. The same
    applies to `share_of_population` on an empty queue.
    """
    buckets = {name: 0 for name in BUCKETS}
    for record in records:
        bucket = record.get("mergeable_bucket")
        if bucket not in buckets:
            # An unclassified record may NOT be dropped: it would silently shrink
            # the population. Attribute it to `unknown`, which is what "we could
            # not classify it" means.
            bucket = "unknown"
        buckets[bucket] += 1
    population = sum(buckets.values())
    known = buckets["mergeable"] + buckets["conflicting"]
    return {
        "population": population,
        **buckets,
        "known": known,
        "share_of_known": (buckets["conflicting"] / known) if known else None,
        "share_of_population": (buckets["conflicting"] / population) if population else None,
    }


def share_percent(share) -> str:
    """`36.2%` for a share dict, or `UNKNOWN` — never a fabricated 0.0%."""
    return _percent(share.get("share_of_known"))


def _percent(value) -> str:
    """`36.2%` for a ratio, or `UNKNOWN` when it is `None`.

    The same rule as `share_percent`, so the human-facing CLI summary cannot
    print a fabricated `0.0%` where the artifact correctly holds `null`.
    """
    if value is None:
        return UNKNOWN
    return f"{value * 100:.1f}%"


def aggregate_paths(paths_by_pr) -> list:
    """Conflicted path -> (number of distinct PRs that conflict on it).

    `paths_by_pr` maps PR number -> iterable of conflicted paths. The per-path
    collection is a SET of PR numbers, so a path repeated inside one PR cannot
    double-count it — that is the property the test pins, and an equivalent
    `for path in set(paths)` here was removed as UNOBSERVABLE after a mutation
    of it produced no red (the PR set already makes it idempotent). Unresolved
    PRs never appear here; their count is reported separately by the caller so
    the aggregation's coverage is never implied to be complete. Sorted by count
    desc then path asc, so the ordering is deterministic.
    """
    counts: dict = {}
    for number, paths in paths_by_pr.items():
        for path in (paths or ()):
            counts.setdefault(path, set()).add(number)
    rows = [
        {"path": path, "pr_count": len(numbers), "prs": sorted(numbers)}
        for path, numbers in counts.items()
    ]
    rows.sort(key=lambda row: (-row["pr_count"], row["path"]))
    return rows


def classify_path(path: str, header_text, declared_by) -> tuple:
    """(kind, evidence) for one path. `kind` ∈ {generated, hand-written, unknown}.

    `header_text` is the first `BANNER_LINES` lines of the file as read from
    `origin/main`, or `None` when it could not be read (deleted, binary, ref
    gone). `declared_by` is the script that declares the path as an output, or
    `None`. A file that cannot be read is `unknown` — never silently
    hand-written, because "we did not look" and "we looked and found no
    banner" are different results.
    """
    if header_text is None:
        return "unknown", "file not readable at origin/main"
    banner = BANNER_RE.search(header_text)
    if banner:
        return (
            "generated",
            f"generated-artifact banner {banner.group(0)!r} in the first {BANNER_LINES} lines",
        )
    if declared_by:
        return "generated", f"declared output of {declared_by}"
    return "hand-written", "no generated banner, no tracked generator declares this path"


def declared_outputs(text: str) -> set:
    """Path literals a script WRITES — resolved from the AST, not from prose.

    WHY NOT "any path literal in a writing script": that rule classified
    `tortoise/sdk.py` as a generated artifact, because `tools/ci_selection.py`
    both writes files and names `sdk.py` in a shared-module LIST. And "any
    module-level path constant" would classify `docs/product/beta-sdk-surface.md`
    as generated, because `tools/sdk_rename_table.py` reads it. The remedy class
    is chosen from this classification, so a path merely *mentioned* or *read*
    by a generator must not enter it. Three resolution paths, in order:

      1. the target expression of a write call, through `Name` bindings
         (`MANIFEST = ROOT / "config" / "ci-surfaces.yml"`);
      2. a parameter threaded into a helper that writes that parameter
         (`register(MANIFEST, ...)` -> `register_tests(manifest_path, ...)` ->
         `manifest_path.write_text(...)`), resolved to a fixed point;
      3. an argparse default reached through `args.<dest>.write_text(...)`
         (`--out`, `default=OUT`).

    Blind spots are stated, not hidden: a path built from a non-literal (an
    environment variable, a loop variable, an `--out` passed at runtime) is not
    resolved, and a write through an already-open handle is caught only at its
    `open()`.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    assignments: dict = {}
    functions: dict = {}
    argparse_defaults: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments[node.target.id] = node.value
        elif isinstance(node, ast.FunctionDef):
            functions[node.name] = node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("--")
        ):
            for keyword in node.keywords:
                if keyword.arg == "default":
                    argparse_defaults[node.args[0].value[2:].replace("-", "_")] = keyword.value

    written_params = _written_params_fixed_point(functions)
    outputs: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
            target = func.value
            outputs |= _string_literals(target, assignments)
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in ("args", "opts", "options")
                and target.attr in argparse_defaults
            ):
                outputs |= _string_literals(argparse_defaults[target.attr], assignments)
        elif isinstance(func, ast.Name) and func.id == "open" and node.args:
            outputs |= _string_literals(node.args[0], assignments)
        elif isinstance(func, ast.Name) and func.id in functions:
            positional = _positional_names(functions[func.id])
            for index, arg in enumerate(node.args):
                if index < len(positional) and positional[index] in written_params[func.id]:
                    outputs |= _string_literals(arg, assignments)
            for keyword in node.keywords:
                if keyword.arg in written_params[func.id]:
                    outputs |= _string_literals(keyword.value, assignments)
    return outputs


def _written_params_fixed_point(functions: dict) -> dict:
    """function -> parameter names it writes, directly OR by passing them on.

    `register(manifest_path, ...)` does not write its own parameter; it hands it
    to `register_tests`, which does. Without this closure the repo's own
    `config/ci-surfaces.yml` registry (the #2 conflict-generating file) reads as
    hand-written. Iterated to a fixed point; a self-recursive helper terminates
    because the set only grows and is bounded by the parameter list.
    """
    written = {name: _written_parameters(fdef) for name, fdef in functions.items()}
    changed = True
    while changed:
        changed = False
        for name, fdef in functions.items():
            positional = _positional_names(fdef)
            params = set(positional) | {a.arg for a in fdef.args.kwonlyargs}
            for call in ast.walk(fdef):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                    continue
                callee = call.func.id
                if callee not in written:
                    continue
                # The pair keys must be the CALLEE's parameter names, which is
                # what `written[callee]` is keyed on. Using the caller's names
                # here only worked while a helper and its callee happened to
                # share a parameter name — a rename silently dropped the
                # output (found by fresh-context review, pinned by
                # test_declared_outputs_follows_a_REnamed_parameter).
                callee_positional = _positional_names(functions[callee])
                pairs = [
                    (callee_positional[i], arg)
                    for i, arg in enumerate(call.args)
                    if i < len(callee_positional)
                ] + [(kw.arg, kw.value) for kw in call.keywords if kw.arg]
                for callee_param, arg in pairs:
                    if callee_param not in written[callee]:
                        continue
                    if (
                        isinstance(arg, ast.Name)
                        and arg.id in params
                        and arg.id not in written[name]
                    ):
                        written[name].add(arg.id)
                        changed = True
    return written


def _positional_names(fdef) -> list:
    args = fdef.args
    return [a.arg for a in (*args.posonlyargs, *args.args)]


def _written_parameters(fdef) -> set:
    """Parameter names this function writes THROUGH (`arg.write_text(...)`)."""
    params = {a.arg for a in (*fdef.args.posonlyargs, *fdef.args.args, *fdef.args.kwonlyargs)}
    written: set = set()
    for node in ast.walk(fdef):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in WRITE_METHODS
            and isinstance(func.value, ast.Name)
            and func.value.id in params
        ):
            written.add(func.value.id)
        elif (
            isinstance(func, ast.Name)
            and func.id == "open"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in params
        ):
            written.add(node.args[0].id)
    return written


def _string_literals(node, assignments: dict, depth: int = 0) -> set:
    """String constants in `node`, following `Name` bindings up to ALIAS_DEPTH."""
    values: set = set()
    if node is None:
        return values
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            values.add(sub.value)
        elif isinstance(sub, ast.Name) and sub.id in assignments and depth < ALIAS_DEPTH:
            values |= _string_literals(assignments[sub.id], assignments, depth + 1)
    return values


def build_generator_index(root: Path = REPO) -> dict:
    """path literal -> [generator scripts] for paths a tracked script WRITES.

    Scans `GENERATOR_DIRS` only. A symlinked directory (this repo's `scripts/`
    is a symlink into agent-infra) is SKIPPED: following it would classify
    paths by a generator that does not live in this repo.
    """
    index: dict = {}
    for dirname in GENERATOR_DIRS:
        base = root / dirname
        if not base.is_dir() or base.is_symlink():
            continue
        for script in sorted(base.rglob("*.py")):
            if not script.is_file():
                continue
            try:
                text = script.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(script.relative_to(root))
            for literal in declared_outputs(text):
                index.setdefault(literal, []).append(rel)
    return index


def lookup_generator(index: dict, path: str):
    """The script(s) that declare `path` as a write target, or None."""
    hits = list(index.get(path, ()))
    basename = path.rsplit("/", 1)[-1]
    if basename != path:
        hits.extend(index.get(basename, ()))
    if not hits:
        for literal, scripts in index.items():
            if "/" in literal and (path.endswith("/" + literal) or literal.endswith("/" + path)):
                hits.extend(scripts)
    if not hits:
        return None
    return ", ".join(sorted(set(hits)))


def parse_ts(value):
    """An ISO-8601 API timestamp -> aware datetime, or None. Never raises."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)  # noqa: UP017 - must import on 3.9


def days_between(now, then):
    """(now - then) in days, or None when either side is unreadable."""
    if now is None or then is None:
        return None
    return (now - then).total_seconds() / 86400.0


def conflict_age_bounds(now, head_commit_time, main_path_touch_time) -> dict:
    """Upper bounds on how long a PR has been conflicted. No lower bound exists.

    The API exposes no "conflicted since" field (stated, not worked around). A
    conflict between branch and `main` needs BOTH sides to have touched the same
    path, so under the simple onset model `onset = max(branch_touch,
    main_touch)` and therefore:

        age = now - onset = min(now - branch_touch, now - main_touch)

    Both components are **upper bounds** on the conflict's age, and the tighter
    one is the estimate. They are reported separately as well as combined,
    because they answer different questions: the branch bound is "this branch
    has not moved since", the main bound is "main has disagreed with it since".
    """
    head_bound = days_between(now, head_commit_time)
    main_bound = days_between(now, main_path_touch_time)
    candidates = [value for value in (head_bound, main_bound) if value is not None]
    return {
        "upper_bound_days": min(candidates) if candidates else None,
        "from_head_commit_days": head_bound,
        "from_main_path_touch_days": main_bound,
    }


def abandonment_split(records, now, threshold_days: float) -> dict:
    """Abandoned vs in-progress, by `idle_days` (the `updated_at` field).

    RULE: a PR is **abandoned** when `now - updated_at >= threshold_days`
    (default 14) and **in-progress** otherwise. `updated_at` is the last
    activity anywhere on the PR (comment, push, label) — the only in-band
    liveness signal the list API carries, and it is used as-is rather than
    pretending to model intent. A PR whose `updated_at` is unreadable is
    neither bucket: it is counted in `unknown_idle`, so the split always
    reconciles to the population.
    """
    idle = [r["idle_days"] for r in records if r.get("idle_days") is not None]
    abandoned = sum(
        1 for r in records
        if r.get("idle_days") is not None and r["idle_days"] >= threshold_days
    )
    unknown_idle = sum(1 for r in records if r.get("idle_days") is None)
    return {
        "rule": (
            f"abandoned iff idle_days (now - updated_at) >= {threshold_days}; "
            "`updated_at` is the last activity on the PR, and is an approximation "
            "of abandonment, not a read of intent"
        ),
        "threshold_days": threshold_days,
        "abandoned": abandoned,
        "in_progress": len(records) - abandoned - unknown_idle,
        "unknown_idle": unknown_idle,
        "idle_days": {
            "median": statistics.median(idle) if idle else None,
            "p90": _percentile(idle, 0.9),
            "max": max(idle) if idle else None,
        },
        "sensitivity": {
            str(days): sum(1 for v in idle if v >= days) for days in SENSITIVITY_IDLE_DAYS
        },
    }


def _percentile(values, fraction: float):
    """Nearest-rank percentile. None on an empty input (never 0)."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def reconcile_population(pulls, details, probes) -> dict:
    """Cross-read reconciliation of the three surfaces. Every field is a count.

    A census whose surfaces disagree must SAY so rather than average it away:
    `details` (per-PR GET) and `probes` (merge-tree) are keyed by PR number, so
    a population entry missing from either is named.
    """
    numbers = [p.get("number") for p in pulls if p.get("number") is not None]
    detail_missing = sorted(n for n in numbers if n not in details)
    probe_missing = sorted(n for n in numbers if n not in probes)
    extra_details = sorted(n for n in details if n not in set(numbers))
    extra_probes = sorted(n for n in probes if n not in set(numbers))
    return {
        "population": len(numbers),
        "distinct": len(set(numbers)),
        "duplicates": sorted({n for n in numbers if numbers.count(n) > 1}),
        "detail_missing": detail_missing,
        "probe_missing": probe_missing,
        "detail_extra": extra_details,
        "probe_extra": extra_probes,
    }


def api_vs_mergetree(records) -> dict:
    """The API field's accuracy against the authoritative merge-tree probe.

    `records` carry `mergeable_bucket` and `mergetree` ∈ {conflicted, clean,
    unresolved}. The under-report count is the interesting one: it is the
    number of PRs the API called mergeable that git says conflict.

    A `null` bucket is NOT an under-report: the API did not answer, so a
    PR that is `unknown` to the API and `conflicted` to git is neither
    under- nor over-reported. It stays visible in the bucket counts and in
    `mergetree_conflicting`; counting it here would inflate the API's error
    rate with the exact false-confidence population this census exists to
    keep separate.
    """
    unresolved = [r["number"] for r in records if r.get("mergetree") == "unresolved"]
    api_conflicting = [r["number"] for r in records if r.get("mergeable_bucket") == "conflicting"]
    true_conflicting = [r["number"] for r in records if r.get("mergetree") == "conflicted"]
    return {
        "api_conflicting": len(api_conflicting),
        "mergetree_conflicting": len(true_conflicting),
        "under_reported": sorted(
            r["number"] for r in records
            if r.get("mergetree") == "conflicted" and r.get("mergeable_bucket") == "mergeable"
        ),
        "over_reported": sorted(
            r["number"] for r in records
            if r.get("mergeable_bucket") == "conflicting" and r.get("mergetree") == "clean"
        ),
        "unresolved": sorted(unresolved),
    }


def assemble_report(pulls, details, probes, now, opts) -> dict:
    """The artifact dict. Pure: every read is passed in.

    `pulls` are list-endpoint entries (the population), `details` maps PR
    number -> per-PR GET detail, `probes` maps PR number -> merge-tree probe
    (`state`, `paths`, `head_commit_time`, `main_path_touch_time`). `opts`
    carries `top`, `abandoned_idle_days`, `read_header` (path -> header text or
    None) and `generator_index`.
    """
    records = []
    for pull in pulls:
        number = pull.get("number")
        detail = details.get(number, {})
        probe = probes.get(number, {})
        mergeable_raw = detail.get("mergeable")
        bucket = classify_mergeable(mergeable_raw)
        updated = parse_ts(detail.get("updated_at") or pull.get("updated_at"))
        created = parse_ts(detail.get("created_at") or pull.get("created_at"))
        head_time = parse_ts(probe.get("head_commit_time"))
        main_touch = parse_ts(probe.get("main_path_touch_time"))
        paths = probe.get("paths") or []
        state = probe.get("state", UNKNOWN)
        record = {
            "number": number,
            "title": (detail.get("title") or pull.get("title") or "")[:160],
            "draft": bool(detail.get("draft", pull.get("draft"))),
            "created_at": detail.get("created_at") or pull.get("created_at"),
            "updated_at": detail.get("updated_at") or pull.get("updated_at"),
            "age_days": days_between(now, created),
            "idle_days": days_between(now, updated),
            "head_sha": (detail.get("head") or {}).get("sha") or (pull.get("head") or {}).get("sha"),
            "api_mergeable": mergeable_raw,
            "api_mergeable_state": detail.get("mergeable_state"),
            "mergeable_bucket": bucket,
            "mergetree": state,
            "conflicted_paths": paths,
            "head_commit_time": probe.get("head_commit_time"),
            "main_path_touch_time": probe.get("main_path_touch_time"),
            "conflict_age": conflict_age_bounds(now, head_time, main_touch),
        }
        records.append(record)

    share = summarize_share(records)
    conflict_rows = [r for r in records if r.get("mergetree") == "conflicted"]
    # The (c) leg is about CONFLICTED PRs: "how many are actually abandoned
    # rather than in progress". The whole-open-queue split is reported as the
    # context the conflicted subset must be read against (a conflicted PR
    # cannot be called abandoned on a threshold the general queue also fails).
    abandoned_conflicted = abandonment_split(conflict_rows, now, opts["abandoned_idle_days"])
    abandoned_queue = abandonment_split(records, now, opts["abandoned_idle_days"])
    paths_by_pr = {r["number"]: r.get("conflicted_paths") or [] for r in conflict_rows}
    aggregated = aggregate_paths(paths_by_pr)
    top = []
    for row in aggregated[: opts["top"]]:
        kind, evidence = classify_path(
            row["path"],
            opts["read_header"](row["path"]),
            lookup_generator(opts["generator_index"], row["path"]),
        )
        top.append({**row, "class": kind, "evidence": evidence})

    generated = sum(1 for row in top if row["class"] == "generated")
    unresolved = [r["number"] for r in records if r.get("mergetree") == "unresolved"]

    report = {
        "schema": SCHEMA,
        "issue": 6138,
        "tool": "tools/queue_conflict_census.py",
        "argv": list(opts.get("argv") or []),
        "snapshot_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),  # noqa: UP017
        "repo": OWNER_REPO,
        "origin_main_sha": opts.get("origin_main_sha"),
        "list_endpoint_mergeable": _bucket_count(pulls, lambda p: p.get("mergeable")),
        "share": share,
        "share_of_known_percent": share_percent(share),
        "api_vs_mergetree": api_vs_mergetree(records),
        "top_conflicting_paths": top,
        "top_summary": {
            "top_n": opts["top"],
            "generated": generated,
            "hand_written": sum(1 for row in top if row["class"] == "hand-written"),
            "unclassifiable": sum(1 for row in top if row["class"] == "unknown"),
            "distinct_conflicted_paths": len(aggregated),
            "conflicting_prs_with_paths": len(paths_by_pr),
        },
        "classifier": {
            "rule": (
                "generated iff (1) the first "
                f"{BANNER_LINES} lines of the file at origin/main contain a marker in "
                f"{BANNER_RE.pattern}, or (2) a tracked script under "
                f"{list(GENERATOR_DIRS)} WRITES this path — resolved from the AST around "
                "write calls, following simple name bindings and arguments threaded "
                "into helper functions that write their parameter, so a path merely "
                "mentioned by a writing script is NOT generated; otherwise "
                "hand-written; unreadable -> unknown"
            ),
            "blind_spot": CLASSIFIER_BLIND_SPOT,
        },
        "age_and_abandonment": {
            "conflicted_prs": abandoned_conflicted,
            "whole_open_queue": abandoned_queue,
            "conflicted_drafts": sum(1 for r in conflict_rows if r.get("draft")),
            "conflict_age_rule": (
                "the API exposes no 'conflicted since'; the reported age is an "
                "UPPER BOUND, min(now - branch head commit time, now - last "
                "origin/main commit touching a conflicted path)"
            ),
            "conflict_age_upper_bound_days": _age_distribution(conflict_rows),
        },
        "population_reconciliation": opts.get("reconciliation", {}),
        "pagination": opts.get("pagination", {}),
        "rate_limit": opts.get("rate_limit", {}),
        "unresolved_prs": unresolved,
        "unresolved_count": len(unresolved),
        "unresolved_reason": opts.get("unresolved_reason", {}),
        "main_moved": bool(opts.get("main_moved", False)),
        "partial": bool(opts.get("partial", False)),
        "exit_code": opts.get("exit_code", 0),
        "prs": records,
        "notes": opts.get("notes", []),
    }
    return report


def _bucket_count(pulls, reader) -> dict:
    counts = {name: 0 for name in BUCKETS}
    for pull in pulls:
        counts[classify_mergeable(reader(pull))] += 1
    return counts


def _age_distribution(rows) -> dict:
    values = [
        row["conflict_age"]["upper_bound_days"]
        for row in rows
        if row.get("conflict_age", {}).get("upper_bound_days") is not None
    ]
    if not values:
        return {"median": None, "p90": None, "max": None, "measured": 0}
    return {
        "median": statistics.median(values),
        "p90": _percentile(values, 0.9),
        "max": max(values),
        "measured": len(values),
    }


# ==========================================================================
# Live reads — every one bounded, every failure UNKNOWN.
# ==========================================================================

def _run(cmd, timeout: int, cwd: Path = REPO):
    """(rc, stdout, stderr). Any failure to even run is rc=None.

    `errors="replace"`: a conflicted path can be BINARY (a PNG/SVG/font in the
    tree), and `git show` on it emits bytes that strict UTF-8 decoding cannot
    decode. Without this, `UnicodeDecodeError` (a `ValueError`, not an
    `OSError`) escaped this helper and crashed the whole census on one binary
    file. Undecodable bytes are now replaced, and `read_header` treats a NUL as
    unreadable, so the honest result is `unknown`, not a traceback.
    """
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def gh_api(path: str, *, paginate: bool = False, timeout: int = 120):
    """Parsed `gh api` JSON, or UNKNOWN. Retries on transport failure only."""
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
    if paginate:
        # `--paginate` without `--slurp` emits ONE JSON DOCUMENT PER PAGE for a
        # list endpoint, which a single json.loads reads as "Extra data".
        cmd += ["--paginate", "--slurp"]
    cmd.append(path)
    for _attempt in range(GH_ATTEMPTS):
        rc, out, _err = _run(cmd, timeout)
        if rc == 0 and out.strip():
            try:
                return json.loads(out)
            except ValueError:
                continue
    return UNKNOWN


def gh_api_headers(path: str, timeout: int = 60, *, attempts: int = 3,
                   backoff_s: float = 2.0):
    """Response headers of a single API call (for the Link/count check).

    Retried like `gh_api`. An unread header collapses to `UNKNOWN`, and
    `enumerate_open_prs` now fails CLOSED on it — so without a retry a single
    transient 403 would turn every run into a spurious exit 2.
    """
    cmd = ["gh", "api", "-i", "-H", "Accept: application/vnd.github+json", path]
    for attempt in range(attempts):
        rc, out, _err = _run(cmd, timeout)
        if rc == 0:
            break
        if attempt + 1 < attempts:
            time.sleep(backoff_s)
    else:
        return UNKNOWN
    headers = {}
    for line in out.splitlines():
        if not line.strip():
            break
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return headers


def rate_limit() -> dict:
    body = gh_api("rate_limit")
    if body is UNKNOWN or not isinstance(body, dict):
        return {"core": UNKNOWN}
    core = (body.get("resources") or {}).get("core")
    if not isinstance(core, dict):
        return {"core": UNKNOWN}
    reset = core.get("reset")
    return {
        "core": {
            "limit": core.get("limit"),
            "remaining": core.get("remaining"),
            "reset": reset,
            "reset_at": (
                datetime.fromtimestamp(reset, tz=timezone.utc).isoformat()  # noqa: UP017
                if isinstance(reset, (int, float)) else None
            ),
        }
    }


def enumerate_open_prs():
    """(pulls, pagination, complete). Complete means: every page full but the
    last, no duplicate number, and the read page count agreeing with the
    `Link: rel="last"` header read independently in a SECOND request.

    A partial list is not a census, so `complete=False` is the caller's signal
    to exit 2 — never to report the partial count as the population.
    """
    path = f"repos/{OWNER_REPO}/pulls?state=open&per_page={PER_PAGE}&sort=created&direction=asc"
    pages = gh_api(path, paginate=True)
    if pages is UNKNOWN or not isinstance(pages, list):
        return [], {"complete": False, "reason": "pagination read failed"}, False
    if pages and not all(isinstance(page, list) for page in pages):
        return [], {"complete": False, "reason": "a page was not a list"}, False
    pulls = [pull for page in pages for pull in page]
    sizes = [len(page) for page in pages]
    page_full_invariant = all(size == PER_PAGE for size in sizes[:-1]) if sizes else True
    numbers = [p.get("number") for p in pulls if isinstance(p, dict)]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    headers = gh_api_headers(
        f"repos/{OWNER_REPO}/pulls?state=open&per_page={PER_PAGE}&page=1"
    )
    # An UNREAD header is not "no Link header". Collapsing the two made the
    # only independent completeness cross-check fail OPEN: `link_last` stayed
    # None and `link_agrees` read True, so a truncated page set ([100, 50] read
    # while the API holds [100, 100, 50]) passed every remaining check and the
    # census reported a share over a fraction of the queue. The page-full
    # invariant cannot catch that on its own — it only requires every page but
    # the last to be full.
    header_read_failed = headers is UNKNOWN
    link_last = None
    if not header_read_failed:
        match = re.search(r'[?&]page=(\d+)>;\s*rel="last"', headers.get("link", ""))
        if match:
            link_last = int(match.group(1))
    link_agrees = (
        not header_read_failed and (link_last is None or link_last == len(pages))
    )
    complete = bool(page_full_invariant and not duplicates and link_agrees and pulls)
    pagination = {
        "per_page": PER_PAGE,
        "pages_read": len(pages),
        "page_sizes": sizes,
        "link_last_page": link_last,
        "link_header_read": not header_read_failed,
        "link_agrees": link_agrees,
        "page_full_invariant": page_full_invariant,
        "duplicates": duplicates,
        "total": len(pulls),
        "complete": complete,
        "reconcile_command": (
            f"gh api --paginate --slurp \"{path}\" | "
            "python3 -c 'import json,sys;print(sum(len(p) for p in json.load(sys.stdin)))'"
        ),
    }
    return pulls, pagination, complete


def read_mergeable(number, *, null_retries: int = 2, null_wait_s: float = 2.0):
    """The per-PR GET. Its `mergeable` is the headline's source.

    The LIST endpoint returns `null` for every PR; only this GET forces the
    computation. A `null` that survives `null_retries` re-reads stays `null` —
    that is the `unknown` bucket, and the count of such PRs is reported.
    """
    detail = {"number": number, "reads": 0, "mergeable": None, "read_ok": False}
    for attempt in range(null_retries + 1):
        body = gh_api(f"repos/{OWNER_REPO}/pulls/{number}")
        detail["reads"] += 1
        if body is UNKNOWN or not isinstance(body, dict):
            continue
        detail["read_ok"] = True
        detail["mergeable"] = body.get("mergeable")
        detail["mergeable_state"] = body.get("mergeable_state")
        detail["title"] = body.get("title")
        detail["draft"] = body.get("draft")
        detail["created_at"] = body.get("created_at")
        detail["updated_at"] = body.get("updated_at")
        detail["head"] = body.get("head") or {}
        if detail["mergeable"] is not None:
            break
        if attempt < null_retries:
            _sleep(null_wait_s)
    return detail


def _sleep(seconds: float):
    time.sleep(seconds)


def ensure_object(sha: str, number) -> bool:
    """Make `sha` locally available. Fetches the PR ref (works across forks),
    then the bare sha as a fallback. False on any doubt."""
    if not sha:
        return False
    rc, _out, _err = _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], 30)
    if rc == 0:
        return True
    for ref in (f"pull/{number}/head", sha):
        rc, _out, _err = _run(
            ["git", "fetch", "--no-write-fetch-head", "--no-tags", "origin", ref],
            FETCH_TIMEOUT,
        )
        if rc == 0:
            rc, _out, _err = _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], 30)
            if rc == 0:
                return True
    return False


def git_version():
    rc, out, _err = _run(["git", "--version"], 30)
    if rc != 0:
        return None
    match = re.search(r"(\d+)\.(\d+)", out)
    return (int(match.group(1)), int(match.group(2))) if match else None


def merge_tree_probe(sha: str):
    """(state, paths, reason). state ∈ {conflicted, clean, unresolved}.

    `unresolved` is a first-class result: a probe that could not run must never
    be reported as `clean`, and the PR's files must never be guessed. `reason`
    names WHY, so a no-merge-base failure can be retried after deepening rather
    than silently counted as a clean probe.
    """
    if not sha:
        return "unresolved", [], "no-head-sha"
    rc, out, err = _run(
        ["git", "merge-tree", "--write-tree", "--name-only", "origin/main", sha],
        GIT_TIMEOUT,
    )
    if rc is None:
        return "unresolved", [], "merge-tree-did-not-run"
    if rc == 0:
        return "clean", [], None
    if rc != 1:
        reason = "no-merge-base" if "unrelated histories" in (err or "") else f"merge-tree-rc={rc}"
        return "unresolved", [], reason
    # rc == 1: conflicts. The paths are the lines between the tree OID (line 1)
    # and the first blank line; the `CONFLICT` prose follows that blank line.
    paths = []
    for line in out.splitlines()[1:]:
        if line == "":
            break
        paths.append(line)
    return "conflicted", paths, None


def deepen_for_merge_base(number, depth: int = DEEPEN_DEPTH) -> bool:
    """Deepen a PR's head ref so `origin/main` and it share a local merge-base."""
    rc, _out, _err = _run(
        [
            "git", "fetch", "--no-write-fetch-head", "--no-tags",
            f"--depth={depth}", "origin", f"pull/{number}/head",
        ],
        FETCH_TIMEOUT,
    )
    return rc == 0


def head_commit_time(sha: str):
    rc, out, _err = _run(["git", "log", "-1", "--format=%cI", sha], 60)
    return out.strip() if rc == 0 and out.strip() else None


def main_path_touch_time(paths):
    """When origin/main last changed any of `paths`. Latest of the per-path
    last touches; None when unreadable or no paths."""
    if not paths:
        return None
    best = None
    for path in paths:
        rc, out, _err = _run(
            ["git", "log", "-1", "--format=%cI", "origin/main", "--", path], 60
        )
        if rc != 0 or not out.strip():
            continue
        if best is None or out.strip() > best:
            best = out.strip()
    return best


def read_header(path: str, lines: int = BANNER_LINES):
    """First `lines` lines of `path` at origin/main, or None.

    Read from the REF, not the working tree: the classification must describe
    the tree the census measured, not whatever the local checkout happens to
    hold.
    """
    rc, out, _err = _run(["git", "show", f"origin/main:{path}"], 60)
    if rc != 0:
        return None
    if "\x00" in out:
        # A binary path has no textual banner to read. `unknown`, not a crash
        # and not a silent `hand-written`.
        return None
    return "\n".join(out.splitlines()[:lines])


def origin_main_sha():
    rc, out, _err = _run(["git", "rev-parse", "origin/main"], 60)
    return out.strip() if rc == 0 and out.strip() else None


# ==========================================================================
# CLI
# ==========================================================================

def census_exit_code(*, complete, main_moved, read_failures, sample) -> int:
    """The process exit contract: 0 = a complete census, 2 = UNKNOWN.

    Any of the four UNKNOWN conditions makes the run's headline numbers
    unsafe to read as a census, so the code says so to the CALLER, not only
    in the artifact's `notes`. Pure and separately callable: this is the one
    number the contract is about, so it must not be reachable only from the
    live path that no test can drive.
    """
    if not complete or main_moved or read_failures or sample:
        return 2
    return 0


def live_census(opts) -> dict:
    """Run the whole census against the live API + local git."""
    notes = []
    git_ver = git_version()
    if git_ver is None or git_ver < MIN_MERGE_TREE:
        notes.append(
            f"git {git_ver} < {MIN_MERGE_TREE}: merge-tree paths unresolved for every PR"
        )

    pulls, pagination, complete = enumerate_open_prs()
    if not complete:
        notes.append("open-PR enumeration INCOMPLETE — not a census")
    if opts["sample"]:
        # A sample is diagnostic only: the cap is applied to the POPULATION
        # before any read, and the run is marked partial so the truncated
        # counts can never be read as the census.
        pulls = pulls[: opts["sample"]]
        pagination = {**pagination, "sampled": opts["sample"], "sampled_from": pagination.get("total")}
        notes.append(f"--sample {opts['sample']}: PARTIAL population, not a census")
    main_before = origin_main_sha()

    details = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=opts["sweep_concurrency"]) as pool:
        futures = {
            pool.submit(read_mergeable, p["number"], null_retries=opts["null_retries"]): p["number"]
            for p in pulls
        }
        for future in concurrent.futures.as_completed(futures):
            details[futures[future]] = future.result()

    probes = {}
    unresolved_reason = {}
    if opts["no_sweep"]:
        notes.append("--no-sweep: no merge-tree probe ran; paths and top-N are empty by choice")
        for pull in pulls:
            probes[pull["number"]] = {
                "state": "unresolved", "paths": [],
                "unresolved_reason": "no-sweep",
            }
            unresolved_reason[str(pull["number"])] = "no-sweep"
    elif git_ver is None or git_ver < MIN_MERGE_TREE:
        for pull in pulls:
            probes[pull["number"]] = {
                "state": "unresolved", "paths": [],
                "unresolved_reason": "git-too-old",
            }
            unresolved_reason[str(pull["number"])] = "git-too-old"
    else:
        # Fetches are SERIAL: concurrent `git fetch` on one repo fights over the
        # same lock files and packs, and a fetch that fails is an unresolved
        # probe — a data loss, not a retry.
        for pull in pulls:
            sha = (pull.get("head") or {}).get("sha")
            if not ensure_object(sha, pull["number"]):
                probes[pull["number"]] = {
                    "state": "unresolved", "paths": [],
                    "unresolved_reason": "head-object-unavailable",
                }
        pending = [
            (pull["number"], (pull.get("head") or {}).get("sha"))
            for pull in pulls
            if pull["number"] not in probes
        ]

        def probe(number_sha):
            number, sha = number_sha
            state, paths, reason = merge_tree_probe(sha)
            return number, {
                "state": state,
                "paths": paths,
                "unresolved_reason": reason,
                "deepened": False,
                "head_commit_time": head_commit_time(sha),
                "main_path_touch_time": main_path_touch_time(paths) if state == "conflicted" else None,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=opts["sweep_concurrency"]) as pool:
            for number, probe_result in pool.map(probe, pending):
                probes[number] = probe_result

        # The deepen retry is a SEPARATE, SERIAL pass: `git fetch` is not
        # safe to run concurrently with other git commands on one repo.
        for number, probe_result in list(probes.items()):
            if probe_result.get("state") != "unresolved":
                continue
            if probe_result.get("unresolved_reason") != "no-merge-base":
                continue
            head_sha = next(
                (p.get("head", {}).get("sha") for p in pulls if p["number"] == number), None
            )
            if not head_sha or not deepen_for_merge_base(number):
                continue
            state, paths, reason = merge_tree_probe(head_sha)
            probes[number] = {
                "state": state,
                "paths": paths,
                "unresolved_reason": reason,
                "deepened": True,
                "head_commit_time": head_commit_time(head_sha),
                "main_path_touch_time": main_path_touch_time(paths) if state == "conflicted" else None,
            }
        for number, probe_result in probes.items():
            if probe_result.get("state") == "unresolved":
                unresolved_reason[str(number)] = probe_result.get(
                    "unresolved_reason", "merge-tree did not run"
                )

    main_after = origin_main_sha()
    main_moved = bool(main_before and main_after and main_before != main_after)
    if main_moved:
        notes.append(f"origin/main moved during the sweep ({main_before} -> {main_after})")

    reconciliation = reconcile_population(pulls, details, probes)
    if reconciliation["detail_missing"]:
        notes.append(f"per-PR GET missing for {len(reconciliation['detail_missing'])} PR(s)")
    if reconciliation["duplicates"]:
        notes.append("DUPLICATE PR numbers in the population")

    read_failures = [n for n, d in details.items() if not d.get("read_ok")]
    if read_failures:
        notes.append(f"mergeable read FAILED for {len(read_failures)} PR(s)")

    exit_code = census_exit_code(
        complete=complete,
        main_moved=main_moved,
        read_failures=read_failures,
        sample=opts["sample"],
    )

    opts.update({
        "origin_main_sha": main_before,
        "pagination": pagination,
        "reconciliation": reconciliation,
        "rate_limit": rate_limit(),
        "main_moved": main_moved,
        "partial": bool(opts["sample"]),
        "exit_code": exit_code,
        "unresolved_reason": unresolved_reason,
        "notes": notes,
        "generator_index": build_generator_index(),
        "read_header": read_header,
    })
    return assemble_report(pulls, details, probes, opts["now"], opts)


def census_from_fixture(path: Path, opts) -> dict:
    """Offline replay. The fixture is the API surface:
    `{pulls, details, probes, now, origin_main_sha, headers, generator_index}`.
    """
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    headers = fixture.get("headers") or {}

    def read_header_from_fixture(path_name):
        return headers.get(path_name)

    opts.update({
        "origin_main_sha": fixture.get("origin_main_sha"),
        "pagination": fixture.get("pagination", {"complete": True, "source": "fixture"}),
        "reconciliation": fixture.get("reconciliation", {}),
        "rate_limit": fixture.get("rate_limit", {}),
        "generator_index": fixture.get("generator_index") or {},
        "read_header": read_header_from_fixture,
        "notes": [*opts.get("notes", []), f"fixture: {path}"],
    })
    now = parse_ts(fixture["now"]) if fixture.get("now") else opts["now"]
    details = {int(k): v for k, v in (fixture.get("details") or {}).items()}
    probes = {int(k): v for k, v in (fixture.get("probes") or {}).items()}
    return assemble_report(fixture.get("pulls") or [], details, probes, now, opts)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Queue conflict census (#6138): the CONFLICTING share of the open PR queue."
    )
    parser.add_argument("--out", default=None,
                        help="artifact path (default docs/ci/queue-conflict-census-<date>.json)")
    parser.add_argument("--top", type=int, default=TOP_N_DEFAULT,
                        help=f"top-N conflicted paths to classify (default {TOP_N_DEFAULT})")
    parser.add_argument("--abandoned-idle-days", type=float,
                        default=ABANDONED_IDLE_DAYS_DEFAULT,
                        help="idle_days at or above which a PR is 'abandoned' (default 14)")
    parser.add_argument("--sweep-concurrency", type=int, default=SWEEP_CONCURRENCY_DEFAULT,
                        help="parallel merge-tree probes (fetches are always serial)")
    parser.add_argument("--null-retries", type=int, default=2,
                        help="re-reads when mergeable is still null (default 2)")
    parser.add_argument("--sample", type=int, default=0, metavar="N",
                        help="DIAGNOSTIC ONLY: cap the population at N and mark the run partial")
    parser.add_argument("--no-sweep", action="store_true",
                        help="API-only: skip merge-tree (no paths, no top-N)")
    parser.add_argument("--no-write", action="store_true", help="do not write the artifact")
    parser.add_argument("--stdout", action="store_true", help="print the artifact JSON to stdout")
    parser.add_argument("--fixture", default=None, help="offline replay from a fixture JSON")
    parser.add_argument("--now", default=None,
                        help="pin the snapshot clock (ISO-8601) for reproducibility tests")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    now = parse_ts(args.now) or datetime.now(timezone.utc)  # noqa: UP017
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)  # noqa: UP017
    opts = {
        "argv": sys.argv[1:],
        "top": args.top,
        "abandoned_idle_days": args.abandoned_idle_days,
        "sweep_concurrency": max(1, args.sweep_concurrency),
        "null_retries": max(0, args.null_retries),
        "sample": args.sample,
        "no_sweep": args.no_sweep,
        "now": now,
    }

    report = (
        census_from_fixture(Path(args.fixture), opts) if args.fixture else live_census(opts)
    )

    share = report["share"]
    print(
        f"open={share['population']} mergeable={share['mergeable']} "
        f"conflicting={share['conflicting']} unknown={share['unknown']} "
        f"share_of_known={report['share_of_known_percent']} "
        f"share_of_population={_percent(share['share_of_population'])}"
        f" unresolved={report['unresolved_count']}"
    )
    age = report["age_and_abandonment"]
    print(
        f"conflicted-of-{share['population']}: "
        f"abandoned={age['conflicted_prs']['abandoned']} "
        f"in_progress={age['conflicted_prs']['in_progress']} "
        f"idle_median={age['conflicted_prs']['idle_days']['median']} "
        f"conflict_age_upper_bound_median={age['conflict_age_upper_bound_days']['median']} "
        f"drafts={age['conflicted_drafts']}"
    )
    if report["top_conflicting_paths"]:
        print(f"top-{len(report['top_conflicting_paths'])} conflicting paths:")
        for row in report["top_conflicting_paths"]:
            print(f"  {row['pr_count']:>3}  {row['class']:<12} {row['path']}")
    for note in report["notes"]:
        print(f"note: {note}", file=sys.stderr)

    if args.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    if not args.no_write:
        out = Path(args.out) if args.out else CENSUS_DIR / (
            f"queue-conflict-census-{report['snapshot_at'][:10]}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"artifact: {out}", file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
