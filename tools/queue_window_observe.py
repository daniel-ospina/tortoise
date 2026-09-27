#!/usr/bin/env python3
"""Read-only queue-window observer for the #5215 merge-throughput plan (Task 3).

**This tool emits RECORDS. It never issues a verdict.** Every threshold and
every exit code belongs to the instrument (`tools/merge_throughput.py`, plan
§10 Task 1, #5705): this observer's output is fed to it via

    python3 tools/merge_throughput.py check batch-size   --input <record.json>
    python3 tools/merge_throughput.py check capacity     --input <record.json>
    python3 tools/merge_throughput.py check parallelism-headroom --input <record.json>

WHY THIS EXISTS (Task 1 / Task 3 contract gap, plan §10)
    The instrument's own `--watch-queue` / `--observe-capacity` modes take a
    SINGLE sample (`_capacity_sample()` -> one dict). M3, M5 and M6, however, are
    defined over **timestamped windows** (plan §5):

      M3  "Derive from timestamped queue check-run / branch create-delete events
           rather than only 60 s polling (polling aliases: short-lived branches
           are invisible, biasing the max downward)."
      M5  "Batch-arrival timestamps + queue depth at formation + the conflicted
           set in the same window."
      M6  "From M3's samples: discarded speculative batches per red."

    A one-sample mode cannot produce any of those. This observer does the
    *observation* the plan assigns to Task 3 and leaves the *judgement* to the
    instrument. It does not import the instrument and does not re-implement any
    polarity or threshold rule.

Read-only: only `gh api` GETs and `git ls-remote`. Never a mutating request.

Usage
    queue_window_observe.py --live --window-hours 8 --out RECORD.json
    queue_window_observe.py --from-json RUNS.jsonl --window-hours 8 \
        --conflicts-json CONFLICTS.json

Capacity is read from the same run corpus (queue wait = `created_at` ->
`run_started_at`; runner-held = `run_started_at` -> `updated_at`), so M4 needs
no separate sampler and no second code path.

Empty / unavailable / partial is `UNKNOWN` (exit 2), never 0 — a failed
enumeration must not read as "nothing to see".
"""
from __future__ import annotations

import argparse
import json as jsonlib
import re
import subprocess

# `timezone.utc`, not `datetime.UTC` (3.11+): the plan's criteria invoke tools
# as `python3`, and this must import on 3.9. Mirrors tools/merge_throughput.py.
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OWNER_REPO = "daniel-ospina/tortoise"

UNKNOWN = "UNKNOWN"

QUEUE_REF_PREFIX = "mergify/merge-queue/"
#: The heavy leg of `python-ci.yml` — the queue-branch run whose duration is the
#: cycle. Named once; the plan's M1/M6 both key on it.
HEAVY_LEG = "Python CI"

#: Mergify's documented default (`max_parallel_checks`), and the effective value
#: when `.mergify.yml` does not set it (plan <C1>). Read from the file below;
#: this constant is the documented default, not an assertion about the config.
DEFAULT_MAX_PARALLEL_CHECKS = 5

#: Branches first seen within this gap belong to one formation wave. Mergify
#: speculates a wave of up to `max_parallel_checks` batches at once, so a wave's
#: size is the observable proxy for the effective parallelism.
WAVE_GAP_SECONDS = 360

#: A queue wait at or above this is a runner-acquisition failure candidate.
STARVATION_MIN_WAIT_MINUTES = 60.0
#: Above this, the wait is a stale/abandoned queue entry, not a concurrency
#: ceiling: its creation moment and its failure moment are different windows, so
#: no single "capacity at first failure" can be read from it. UNKNOWN, not a guess.
STALE_QUEUE_WAIT_MINUTES = 360.0

#: Sampling caveat recorded with every event-derived number (M3's own words).
SAMPLING_CAVEAT = (
    "Event-derived: queue-branch create/delete and per-run start/end timestamps "
    "from the Actions API. The wave grouping is bounded to a "
    f"{WAVE_GAP_SECONDS}s formation gap, so two genuinely separate waves closer "
    "than that gap are merged into one; per-ref polling would alias SHORT-LIVED "
    "branches downward (M3), which is why the event timestamps, not the poll, "
    "set the value."
)


# ---------------------------------------------------------------------------
# Typed helpers (mirrors the instrument's stdlib-only discipline).
# ---------------------------------------------------------------------------

def _ts(value):
    """ISO-8601 -> aware datetime, else None. Z is normalised."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017 - must import on 3.9 (see header)
    return dt


def _minutes(a, b):
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 60.0


def _iso(dt):
    return None if dt is None else dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


# ---------------------------------------------------------------------------
# Pure analysis — every function below is unit-tested without a network.
# ---------------------------------------------------------------------------

def parse_batch_title(title):
    """`merge queue: checking <PRs> on main (<sha>)[, stacked on <PRs>]`.

    Returns `(batch_prs, stacked_prs)`. The batch is the group being tested;
    `stacked on` names the speculative batches BELOW it in the stack and is NOT
    part of this batch's size (the cycle-10 draft's "4-PR batches" were this
    misread).
    """
    text = title or ""
    head = re.match(r"\s*merge queue:\s*checking (.+?)\s+on main", text)
    if head is None:
        return [], []
    batch = [int(n) for n in re.findall(r"#(\d+)", head.group(1))]
    stacked_match = re.search(r"stacked on\s+(.+)$", text)
    stacked = (
        [int(n) for n in re.findall(r"#(\d+)", stacked_match.group(1))]
        if stacked_match is not None
        else []
    )
    return batch, stacked


def queue_branch_runs(runs):
    """The subset of `runs` whose head branch is a merge-queue batch branch."""
    return [
        r for r in runs
        if isinstance(r, dict)
        and str(r.get("head_branch") or "").startswith(QUEUE_REF_PREFIX)
    ]


def first_seen(runs):
    """branch -> earliest `created_at` (aware datetime)."""
    seen = {}
    for r in runs:
        branch = r.get("head_branch")
        created = _ts(r.get("created_at"))
        if not branch or created is None:
            continue
        if branch not in seen or created < seen[branch]:
            seen[branch] = created
    return seen


def formations(runs):
    """One entry per queue branch, ordered by first sighting."""
    by_branch = {}
    for r in runs:
        branch = r.get("head_branch")
        if not branch:
            continue
        by_branch.setdefault(branch, r)
    seen = first_seen(runs)
    out = []
    for branch, at in seen.items():
        sample = by_branch.get(branch) or {}
        batch, stacked = parse_batch_title(sample.get("display_title"))
        out.append({
            "branch": branch,
            "at": at,
            "batch_prs": batch,
            "stacked_prs": stacked,
            "size": len(batch),
            "main_sha": _main_sha_from_title(sample.get("display_title")),
        })
    out.sort(key=lambda f: (f["at"], f["branch"]))
    return out


def _main_sha_from_title(title):
    m = re.search(r"on main \(([0-9a-f]{7,40})\)", title or "")
    return m.group(1) if m else None


def cluster_waves(items, gap_seconds=WAVE_GAP_SECONDS):
    """Group time-ordered formations into waves separated by more than `gap`."""
    waves = []
    current = []
    for item in sorted(items, key=lambda f: f["at"]):
        if current and (item["at"] - current[-1]["at"]).total_seconds() > gap_seconds:
            waves.append(current)
            current = []
        current.append(item)
    if current:
        waves.append(current)
    return waves


def repeatable_wave_size(waves):
    """The largest wave size observed in MORE THAN ONE wave.

    "Max observed alone is never reported as the effective value" (M3). A
    one-off burst (a 10 or 15 branch wave) cannot set the value; a size seen
    twice is behaviour, not an accident. UNKNOWN when no size repeats.
    """
    counts = {}
    for wave in waves:
        size = len(wave)
        counts[size] = counts.get(size, 0) + 1
    repeatable = [size for size, n in counts.items() if n >= 2]
    if not repeatable:
        return None, counts
    return max(repeatable), counts


def sweep_max_concurrency(intervals):
    """Max number of intervals alive at once, and when. Pure sweep line.

    Returns `(None, None)` when NO interval could be formed — an empty read is
    UNKNOWN, never `0`, so a failed enumeration cannot masquerade as "nothing
    queued, nothing running" (which a threshold check would pass).
    """
    events = []
    for start, end in intervals:
        if start is None or end is None or end < start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    if not events:
        return None, None
    events.sort(key=lambda e: (e[0], e[1]))
    cur = best = 0
    best_at = None
    for at, delta in events:
        cur += delta
        if cur > best:
            best = cur
            best_at = at
    return best, best_at


def queue_intervals(runs, now=None):
    """[(created_at, run_started_at_or_now)] — the window a run spent waiting.

    A run that is STILL queued has no `run_started_at`; it is closed at `now`
    rather than dropped, because a jammed queue is precisely the M4 shape and
    dropping it would read as "nothing queued".
    """
    now = now or datetime.now(timezone.utc)  # noqa: UP017 - must import on 3.9
    out = []
    pending = {"queued", "pending", "requested", "waiting"}
    for r in runs:
        created, started = _ts(r.get("created_at")), _ts(r.get("run_started_at"))
        if created is None:
            continue
        if started is not None and started > created:
            # A zero-length wait is a DEGENERATE interval (it never queued).
            # Keeping it would let `sweep_max_concurrency` read 0 — the
            # fail-open this function's guard exists to prevent.
            out.append((created, started))
        elif started is None and r.get("status") in pending and now >= created:
            # ONLY a run that is still QUEUED. A completed run with no
            # `run_started_at` (`startup_failure`, cancelled-while-queued) never
            # held a queue slot; treating it as "queued until now" would inflate
            # `queued` / `oldest_minutes` / `capacity_at_first_failure` — and
            # I9's headroom is computed from the last of those, so the bias is
            # fail-OPEN (a higher capacity reads as more headroom). A run whose
            # `status` is absent is not classified as queued either: fail closed.
            out.append((created, now))
    return out


def running_intervals(runs, now=None):
    """[(run_started_at, updated_at_or_now)] — the window a run held a runner."""
    now = now or datetime.now(timezone.utc)  # noqa: UP017 - must import on 3.9
    out = []
    for r in runs:
        started, updated = _ts(r.get("run_started_at")), _ts(r.get("updated_at"))
        if started is None:
            continue
        if r.get("status") == "in_progress" and now > started:
            # A currently-running job holds a runner until NOW: `updated_at` only
            # advances on a status transition, so using it would truncate the
            # window and bias concurrency downward.
            out.append((started, now))
        elif updated is not None and updated > started:
            out.append((started, updated))
    return out


def oldest_queued_minutes(intervals, now):
    """The exact max age of the oldest still-queued run, ignoring `now`.

    Analytic rather than grid-sampled: a sample grid anchors at the first
    `created_at`, so it never evaluates an interval's end and systematically
    UNDER-reports (a downward bias on a `--max` threshold can only flip
    red->green).
    """
    best = 0.0
    best_at = None
    for start, end in intervals:
        if start is None or end is None or end < start:
            continue
        age = (end - start).total_seconds() / 60.0
        if age > best:
            best = age
            best_at = end
    if best_at is None:
        return None, None
    return best, best_at


def queue_depth_by_formation(formations_list, queue_runs, now=None):
    """Distinct queue branches with an in-flight run at each formation moment.

    M5 asks for the queue depth AT FORMATION, not a momentary sample taken
    somewhere else. A branch counts from the moment it ENTERED THE QUEUE
    (`created_at`) until it finished (or until `now`, if it is still live), so a
    branch waiting for a runner still counts and a branch is counted at its own
    formation (every formation therefore reads depth >= 1).
    """
    now = now or datetime.now(timezone.utc)  # noqa: UP017 - must import on 3.9
    intervals = []
    for r in queue_runs:
        branch = r.get("head_branch")
        if not branch:
            continue
        # Start at the QUEUE ENTRY (`created_at`), not at `run_started_at`: a
        # branch waiting for a runner IS in the queue, so it must count at a
        # formation that happens while it waits. Starting at `run_started_at`
        # excluded exactly that window and could report depth 0 — "the queue was
        # empty" — at a formation that was queued.
        start = _ts(r.get("created_at")) or _ts(r.get("run_started_at"))
        if start is None:
            continue
        status = r.get("status")
        if status in {"in_progress", "queued", "pending", "requested", "waiting"}:
            # A live branch holds its slot until NOW; `updated_at` only advances
            # on a status transition, so using it would truncate the interval —
            # the same bias `running_intervals` guards against.
            end = now
        else:
            end = _ts(r.get("updated_at"))
        if end is not None and end >= start:
            intervals.append((branch, start, end))
    return [
        len({b for b, s, e in intervals if s <= f["at"] <= e})
        for f in formations_list
    ]


def bisection_singles(formations_list):
    """Size-1 formations whose PR appeared in an EARLIER BATCH (size >= 2).

    Mergify re-tests a red batch by splitting it, so these are bisection re-runs
    — NOT evidence that batching failed to pair. Two guards keep the split
    honest: the prior formation must be a real batch (a repeat single is a
    formation, not a bisection), and the lookback is unbounded in time (a fixed
    2 h bound misclassifies a split whose batch is older — PR #4617 forms alone
    twice 3 h 05 apart, the second of which a 2 h bound would call fresh).
    """
    out = []
    for i, f in enumerate(formations_list):
        if f["size"] != 1:
            continue
        pr = f["batch_prs"][0]
        prior_batch = any(
            g["size"] >= 2 and pr in g["batch_prs"]
            for g in formations_list[:i]
            if g["at"] <= f["at"]
        )
        if prior_batch:
            out.append(f)
    return out


def red_waves(waves, runs):
    """Per wave: the head batch's heavy-leg verdict + discarded speculations.

    In serial mode a red invalidates the speculative batches above it, so a red
    head of an N-branch wave wastes N-1 speculative validations (plan <C3> term
    iii). This is the M6 term; the plan says MEASURE it, not assume it.

    `red` is `True`/`False`/**`None`**: a head whose heavy leg was never OBSERVED
    is UNKNOWN, never "not red" — treating an unobserved head as green would
    report zero waste for exactly the in-flight wave the capture caught.
    """
    heavy = {}
    for r in runs:
        if r.get("name") != HEAVY_LEG:
            continue
        branch = r.get("head_branch")
        conclusion = r.get("conclusion")
        if not branch or not conclusion:
            continue
        # Newest attempt per (branch, name): a batch that reds and is then
        # re-run green is not a red wave.
        attempt = r.get("run_attempt") or 1
        key = (branch, r.get("name"))
        if key not in heavy or attempt > heavy[key][0]:
            heavy[key] = (attempt, conclusion)
    out = []
    for wave in waves:
        head = wave[0]
        conclusion = heavy.get((head["branch"], HEAVY_LEG))
        verdict = None if conclusion is None else conclusion[1]
        red = None if verdict is None else (verdict == "failure")
        out.append({
            "at": head["at"],
            "wave_size": len(wave),
            "head_branch": head["branch"],
            "head_batch_prs": head["batch_prs"],
            "heavy_leg_conclusion": verdict,
            "red": red,
            "discarded_speculative": ((len(wave) - 1) if red else 0),
        })
    return out


def configured_max_parallel_checks(config_path=None):
    """Read `max_parallel_checks` from `.mergify.yml`; absent => the default.

    A tiny line scan rather than a YAML dependency: the instrument is
    stdlib-only and this observer stays stdlib-only too. Returns
    `(value, source)` where source is "config" or "documented-default".
    """
    path = Path(config_path) if config_path else REPO / ".mergify.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_MAX_PARALLEL_CHECKS, "documented-default (config unreadable)"
    for line in text.splitlines():
        # A trailing comment is legal YAML and must not defeat the scan.
        m = re.match(r"\s*max_parallel_checks:\s*[\"']?(\d+)[\"']?\s*(?:#.*)?$", line)
        if m:
            return int(m.group(1)), "config"
    return DEFAULT_MAX_PARALLEL_CHECKS, "documented-default (not set in .mergify.yml)"


# ---------------------------------------------------------------------------
# Record assembly.
# ---------------------------------------------------------------------------

def build_record(runs, window_hours, confirmed_max_parallel=None, now=None,
                 conflicted_set=None, resolved_main_sha=None, config_path=None,
                 corpus_truncated=None):
    """Assemble the observation record from a list of workflow-run dicts."""
    now = now or datetime.now(timezone.utc)  # noqa: UP017 - must import on 3.9 (see header)
    all_runs = [r for r in runs if isinstance(r, dict)]
    corpus_times = [t for t in (_ts(r.get("created_at")) for r in all_runs) if t is not None]
    corpus_first = min(corpus_times) if corpus_times else None
    queue_runs = queue_branch_runs(all_runs)
    if not queue_runs:
        return {"status": UNKNOWN, "reason": "no merge-queue runs enumerated"}

    cutoff = now - timedelta(hours=window_hours)

    def in_window_of(corpus):
        # An unparseable `created_at` is EXCLUDED, never treated as `now` — that
        # would pull every malformed row into the window.
        return [
            r for r in corpus
            if (lambda t: t is not None and t >= cutoff)(_ts(r.get("created_at")))
        ]

    in_window = in_window_of(queue_runs)
    if not in_window:
        # NO silent widening: reporting out-of-window data under the requested
        # window is the fail-open shape the UNKNOWN policy exists to stop.
        return {"status": UNKNOWN,
                "reason": f"no merge-queue runs inside the {window_hours}h window"}

    # Capacity (M4) is a property of the RUNNERS, not of the queue: a starved
    # non-queue run is exactly the signal M4 needs, so it reads the whole
    # corpus. Parallelism/batches (M3/M5/M6) read the queue branches only.
    all_in_window = in_window_of(all_runs)
    if not all_in_window:
        return {"status": UNKNOWN,
                "reason": f"no runs inside the {window_hours}h window"}

    forms = formations(in_window)
    # Bisections FIRST: a size-1 re-run must not be clustered into a wave, or it
    # inflates the wave size and hence the effective parallelism (M3).
    bisections = bisection_singles(forms)
    bisection_branches = {f["branch"] for f in bisections}
    formed = [f for f in forms if f["branch"] not in bisection_branches]
    formed_sizes = [f["size"] for f in formed]
    # M5's "queue depth at formation": built BEFORE clustering so a formation's
    # depth is read at its own timestamp, not at its wave's.
    depths = queue_depth_by_formation(forms, in_window, now)
    depth_by_branch = {f["branch"]: depths[i] for i, f in enumerate(forms)}

    waves = cluster_waves(formed)
    wave_size, wave_counts = repeatable_wave_size(waves)
    effective = wave_size
    basis = (
        "largest wave size observed in >=2 waves"
        if effective is not None
        else "no repeatable wave in the window: effective is UNKNOWN"
    )
    if effective is None and confirmed_max_parallel is not None:
        # Only when no wave repeats does a direct ref listing stand in — and it
        # still cannot be the "max observed" reading on its own (M3).
        effective = confirmed_max_parallel
        basis = "direct ref listing (no repeatable wave in the window)"

    size_dist = {}
    for f in formed:
        size_dist[f["size"]] = size_dist.get(f["size"], 0) + 1

    reds = red_waves(waves, in_window)
    discarded = sum(w["discarded_speculative"] for w in reds)
    red_count = sum(1 for w in reds if w["red"] is True)
    unobserved_waves = sum(1 for w in reds if w["red"] is None)
    batch_waves = [w for w in waves if len(w) >= 2]

    heavy = [r for r in in_window if r.get("name") == HEAVY_LEG and r.get("conclusion")]
    heavy_dist = {}
    for r in heavy:
        heavy_dist[r["conclusion"]] = heavy_dist.get(r["conclusion"], 0) + 1

    # ---- capacity (M4) -------------------------------------------------
    q_iv = queue_intervals(all_in_window, now)
    r_iv = running_intervals(all_in_window, now)
    max_queued, _qat = sweep_max_concurrency(q_iv)
    max_running, _rat = sweep_max_concurrency(r_iv)
    max_combined, _cat = sweep_max_concurrency(q_iv + r_iv)
    oldest, oldest_at = oldest_queued_minutes(q_iv, now)

    starvation = [
        r for r in all_in_window
        # "Demonstrably failed to acquire a runner" means the run NEVER HELD one
        # and its workflow never created a job: `startup_failure` is the one
        # API signal for that. A run that waited and then RAN was DELAYED, not
        # starved; a `cancelled` run was interrupted, not starved.
        if r.get("conclusion") == "startup_failure"
    ]
    # The candidates are recorded so the doc's specific-run claim is verifiable
    # from the committed artifact, not only from prose (a P2 review finding).
    starvation_candidates = [
        {
            "run_id": r.get("id"),
            "name": r.get("name"),
            "created_at": _iso(_ts(r.get("created_at"))),
            "conclusion": r.get("conclusion"),
            "wait_minutes": (
                None if _minutes(_ts(r.get("created_at")),
                                 _ts(r.get("run_started_at"))) is None
                else round(_minutes(_ts(r.get("created_at")),
                                    _ts(r.get("run_started_at"))), 1)
            ),
        }
        for r in starvation
    ]
    # Delays are reported separately from acquisition failures (M4's own split).
    waits = [
        w for w in (
            _minutes(_ts(r.get("created_at")), _ts(r.get("run_started_at")))
            for r in all_in_window
        ) if w is not None
    ]
    max_queue_wait = round(max(waits), 1) if waits else None
    delayed_over_60 = sum(1 for w in waits if w >= STARVATION_MIN_WAIT_MINUTES)
    capacity_at_first_failure = UNKNOWN
    capacity_at_first_failure_batches = UNKNOWN
    capacity_reason = "no run in the window failed to acquire a runner"
    if starvation:
        # Walk candidates in time order and use the FIRST whose wait is
        # localisable; a stale earliest entry must not discard a usable later
        # one (I9 says UNKNOWN only when NO sample exists).
        starvation.sort(key=lambda r: _ts(r.get("created_at")) or now)
        for candidate in starvation:
            created = _ts(candidate.get("created_at"))
            wait = _minutes(created, _ts(candidate.get("run_started_at")))
            if wait is None or wait > STALE_QUEUE_WAIT_MINUTES:
                continue
            active = [
                (s, e) for s, e in (q_iv + r_iv) if s is not None and e is not None
                and s <= created <= e
            ]
            # I9 compares `capacity_at_first_failure` against
            # `configured_max_parallel_checks`, but the two are in DIFFERENT
            # UNITS: the former counts concurrent workflow RUNS, the latter
            # concurrent queue BATCHES (each batch emits ~7 runs). Record the
            # batch-unit reading too, so the comparison can be made in one unit
            # instead of silently flipping its verdict.
            head_branches = {
                r.get("head_branch") for r in all_in_window
                if r.get("head_branch")
                and str(r.get("head_branch")).startswith(QUEUE_REF_PREFIX)
                and (lambda s, e: s is not None and e is not None
                     and s <= created <= e)(
                         _ts(r.get("run_started_at")), _ts(r.get("updated_at")))
            }
            capacity_at_first_failure = len(active)
            capacity_at_first_failure_batches = len(head_branches)
            capacity_reason = (
                f"concurrency at the earliest LOCALIZABLE failed-to-start run "
                f"({candidate.get('name')} @ {_iso(created)}, wait {wait:.0f} min): "
                f"{capacity_at_first_failure} concurrent runs = "
                f"{capacity_at_first_failure_batches} concurrent queue batches "
                f"({len(starvation)} acquisition-failure candidate(s) in the window)"
            )
            break
        else:
            capacity_reason = (
                f"all {len(starvation)} failed-to-start candidate(s) in the window "
                f"have an unlocalizable wait (none, or above the "
                f"{STALE_QUEUE_WAIT_MINUTES:.0f}-min stale bound): their creation "
                "and their failure are different windows, so no "
                "capacity-at-a-moment can be read (UNKNOWN, never a guess)"
            )
    configured, configured_source = configured_max_parallel_checks(config_path)
    verified_at = _iso(now)

    # I9's `headroom = capacity_at_first_failure - configured`. Recorded in BOTH
    # units because the two readings are in different ones (runs vs batches) and
    # the verdict flips between them — the unit defect this lane files as F4.
    if capacity_at_first_failure == UNKNOWN:
        headroom = UNKNOWN
        headroom_reason = (
            "capacity_at_first_failure is UNKNOWN: I9 refuses (exit 2), never "
            "passes (plan §3 I9)"
        )
    else:
        headroom = capacity_at_first_failure - configured
        headroom_reason = (
            f"{capacity_at_first_failure} concurrent runs - {configured} batches; "
            "UNITS DIFFER (see F4) — the batch-unit headroom below is the "
            "one comparable to `configured_max_parallel_checks`"
        )
    headroom_batches = (
        UNKNOWN
        if capacity_at_first_failure_batches == UNKNOWN
        else capacity_at_first_failure_batches - configured
    )

    def num_or_unknown(value):
        # An empty interval read is UNKNOWN, never 0. The instrument's field
        # check rejects a non-numeric value, but ACCEPTS a legitimate 0 — so a
        # failed enumeration returned as 0 would pass it.
        return UNKNOWN if value is None else value

    capacity = {
        "queued": num_or_unknown(max_queued),
        "in_progress": num_or_unknown(max_running),
        "combined": num_or_unknown(max_combined),
        "headroom": headroom,
        "headroom_batches": headroom_batches,
        "headroom_reason": headroom_reason,
        "oldest_minutes": None if oldest is None else round(oldest, 1),
        "oldest_at": _iso(oldest_at),
        "capacity_at_first_failure": capacity_at_first_failure,
        "capacity_at_first_failure_batches": capacity_at_first_failure_batches,
        "capacity_at_first_failure_reason": capacity_reason,
        "capacity_at_first_failure_candidates": starvation_candidates,
        "max_queue_wait_minutes": max_queue_wait,
        "runs_delayed_over_60min": delayed_over_60,
        "configured_max_parallel_checks": configured,
        "configured_source": configured_source,
        "samples": {"queued": num_or_unknown(max_queued),
                    "in_progress": num_or_unknown(max_running),
                    "queued_plus_running": num_or_unknown(max_combined)},
        "verified_at": verified_at,
        "records": {
            key: {"verified_at": verified_at, "source": "Actions API run timestamps"}
            for key in ("queued", "in_progress", "oldest_minutes",
                        "capacity_at_first_failure")
        },
    }

    # The check-name payloads are built ONCE and referenced from both the
    # observation sections (`batches`, `capacity`) and the instrument's
    # check-name keys (`batch-size`, `parallelism-headroom`), so the two can
    # never drift apart.
    batch_size_payload = {
        "events": len(formed),
        "batch_sizes": formed_sizes,
        "max_batch_size": max(formed_sizes) if formed_sizes else None,
        "bisection_singles": len(bisections),
        "conflicted_set": conflicted_set,
        "verified_at": verified_at,
    }

    return {
        "status": "OK",
        "window": {
            "hours": window_hours,
            "end": verified_at,
            "queue_runs_in_window": len(in_window),
            "queue_branches_in_window": len(formed),
            # The corpus read itself, so the provenance claim is verifiable from
            # the committed record and not only from a re-run of the dump.
            "corpus_runs": len(all_runs),
            "corpus_first_run_at": _iso(corpus_first),
            # TRI-STATE: True/False are the observer's own `--live` read cap; a
            # `--from-json` replay did not produce the dump, so its completeness
            # is UNKNOWN (None) — a `false` there would be an unsupported claim.
            "truncated": corpus_truncated,
            "main_sha": resolved_main_sha or max(
                (f["main_sha"] for f in forms if f["main_sha"]), default=None
            ),
            "main_sha_source": (
                "resolved origin/main at capture"
                if resolved_main_sha
                else "unresolved: max() over queue-title shas (provenance only)"
            ),
        },
        "parallelism": {
            "effective_max_parallel_checks": effective if effective else UNKNOWN,
            "configured_max_parallel_checks": configured,
            "configured_source": configured_source,
            "basis": basis,
            "wave_sizes": [
                {"at": _iso(w[0]["at"]), "size": len(w)} for w in waves
            ],
            "wave_size_counts": {str(k): v for k, v in sorted(wave_counts.items())},
            "max_observed_batches": max((len(w) for w in waves), default=0),
            "live_queue_refs_at_observation": confirmed_max_parallel,
            "live_queue_refs_note": (
                "a momentary count, recorded as CORROBORATION only: a queue branch "
                "is short-lived, so this number is not the effective value"
            ),
            "formation_size_distribution": {
                str(k): v for k, v in sorted(size_dist.items())
            },
            "sampling_caveat": SAMPLING_CAVEAT,
        },
        "batches": {
            **batch_size_payload,
            "bisection_prs": [f["batch_prs"][0] for f in bisections],
            "max_queue_depth_at_formation": (
                max(depth_by_branch.values()) if depth_by_branch else None
            ),
            "formations": [
                {
                    "at": _iso(f["at"]), "branch": f["branch"], "size": f["size"],
                    "batch_prs": f["batch_prs"], "stacked_prs": f["stacked_prs"],
                    "main_sha": f["main_sha"],
                    "bisection": f["branch"] in bisection_branches,
                    "queue_depth_at_formation": depth_by_branch.get(f["branch"]),
                }
                for f in forms
            ],
        },
        "invalidations": {
            "red_waves": red_count,
            "waves": len(waves),
            "batch_waves": len(batch_waves),
            "red_batch_waves": sum(
                1 for w in reds if w["red"] and w["wave_size"] >= 2
            ),
            "discarded_speculative": discarded,
            "unobserved_waves": unobserved_waves,
            "heavy_leg_conclusions": heavy_dist,
            "per_wave": [
                {
                    "at": _iso(w["at"]), "wave_size": w["wave_size"],
                    "red": w["red"],
                    "head_batch_prs": w["head_batch_prs"],
                    "heavy_leg_conclusion": w["heavy_leg_conclusion"],
                    "discarded_speculative": w["discarded_speculative"],
                }
                for w in reds
            ],
            "waste_ratio": (
                round(discarded / len(batch_waves), 2) if batch_waves else None
            ),
            "verified_at": verified_at,
        },
        "capacity": capacity,
        # Check-name keys so the record is directly consumable by the instrument:
        # `check <name> --input <record>` unwraps by CHECK NAME, not by section.
        "batch-size": batch_size_payload,
        "parallelism-headroom": capacity,
        "verified_at": verified_at,
    }


# ---------------------------------------------------------------------------
# Live reads (read-only).
# ---------------------------------------------------------------------------

def _gh_jsonl(args, timeout=180):
    """Run a read-only `gh api` and return parsed JSON lines.

    Returns a list on success and **None** on any failure (missing binary,
    non-zero exit, timeout, or an unparseable line). `None` is distinct from
    `[]` so a caller can never mistake a failed read for an empty one.
    """
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(jsonlib.loads(line))
        except ValueError:
            # A torn page or a truncated file: the read is NOT complete.
            return None
    return out


def fetch_runs(pages=8):
    """Workflow runs, newest first, as JSON objects. Read-only.

    Returns a `(runs, truncated)` pair: `runs` is `None` on a failed read, and
    `truncated` is True when the read hit the page cap (so a busy window cannot
    be silently analysed over a subset).
    """
    lines = _gh_jsonl([
        "gh", "api",
        f"repos/{OWNER_REPO}/actions/runs?per_page=100",
        "--paginate", "--jq", ".workflow_runs[]",
    ])
    if lines is None:
        return None, False
    cap = pages * 100
    return lines[:cap], len(lines) > cap


def live_queue_refs():
    """The merge-queue refs that exist right now (corroboration for M3)."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{QUEUE_REF_PREFIX}*"],
            capture_output=True, text=True, cwd=str(REPO), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line.split("refs/heads/")[-1] for line in proc.stdout.splitlines() if line]


def resolve_origin_main():
    """The resolved `origin/main` sha at capture (read-only). None on failure."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "origin", "refs/heads/main"],
            capture_output=True, text=True, cwd=str(REPO), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.split()[0]


def read_jsonl(path):
    """Parse a JSONL file. Returns None if any line fails to parse."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(jsonlib.loads(line))
                except ValueError:
                    return None
    except OSError:
        return None
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="queue_window_observe.py",
        description="Read-only merge-queue window observer (emits records; "
                    "the instrument owns verdicts).",
    )
    parser.add_argument("--window-hours", type=float, default=8.0)
    parser.add_argument("--from-json", help="a JSONL of workflow-run objects")
    parser.add_argument("--live", action="store_true",
                        help="read workflow runs via gh api (read-only)")
    parser.add_argument("--out", help="write the record here (default stdout)")
    parser.add_argument("--confirm-refs", action="store_true",
                        help="confirm the effective parallelism against the merge-queue "
                             "refs that exist right now (git ls-remote, read-only)")
    parser.add_argument("--conflicts-json", help="the instrument's --json .conflicts")
    parser.add_argument("--main-sha", help="the resolved origin/main sha for provenance "
                                            "(a --from-json replay is hermetic and does not "
                                            "shell out unless this is given)")
    parser.add_argument("--as-of", help="ISO timestamp to treat as 'now' — pins the window end "
                                        "to the corpus's coverage so a replay is reproducible "
                                        "and recent runs missing from the dump cannot look "
                                        "like absent demand (default: the current time)")
    args = parser.parse_args(argv)

    truncated = False
    if args.from_json:
        runs = read_jsonl(args.from_json)
    elif args.live:
        runs, truncated = fetch_runs()
    else:
        parser.error("one of --from-json / --live is required")

    def emit_unknown(reason, truncated=truncated):
        # Carry the READ-COMPLETENESS provenance on every emitted record, not only
        # on the post-`build_record` path: a truncated `--live` read that then hits
        # an unreadable conflicts input would otherwise lose it.
        body = {"status": UNKNOWN, "reason": reason}
        if truncated:
            body["truncated"] = True
        if args.out:
            # Always write: a stale record left on disk is read as current.
            Path(args.out).write_text(jsonlib.dumps(body, indent=2) + "\n",
                                      encoding="utf-8")
        print(jsonlib.dumps(body))
        return 2

    if runs is None:
        return emit_unknown("run enumeration failed or contained an unparseable line")
    if not runs:
        return emit_unknown("empty run enumeration")

    conflicted = None
    # `--as-of` must be a REAL timestamp or refuse: silently falling back to
    # wall-clock `now` would make the run reproducible in shape but not in
    # content, which is the exact non-reproducibility the flag exists to stop.
    as_of = None
    if args.as_of:
        as_of = _ts(args.as_of)
        if as_of is None:
            parser.error(f"--as-of is not a parseable ISO-8601 timestamp: {args.as_of!r}")
    if args.conflicts_json:
        try:
            data = jsonlib.loads(Path(args.conflicts_json).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A requested conflicts read that cannot be read is UNKNOWN (exit 2),
            # never `conflicted_set: null` at exit 0 — that would read as
            # "conflicts were checked and there were none".
            return emit_unknown("conflicts input unreadable or malformed")
        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            return emit_unknown("conflicts input carries no `items` list")
        # Mirror the instrument's own completeness guard (`_failed_read` +
        # enumeration reconciliation): a well-formed but FAILED or PARTIAL read is
        # still UNKNOWN, never a smaller population silently recorded as the whole
        # (the earlier fix only rejected an unPARSEABLE file).
        if data.get("read_ok", True) is not True:
            return emit_unknown("conflicts input reports a failed read (read_ok != true)")
        if data.get("incomplete_results") not in (None, False):
            return emit_unknown("conflicts input is incomplete (incomplete_results)")
        total = data.get("total", data.get("total_count"))
        if not isinstance(total, int) or len(data["items"]) != total:
            return emit_unknown(
                "conflicts enumeration did not reconcile to its total: "
                f"{len(data['items'])} items vs {total!r}"
            )
        # The instrument invalidates a sweep whose `origin/main` moved mid-read;
        # its `.conflicts` PROJECTION drops `main_moved` and would leave only
        # `{total: 0, items: []}`, so both the raw flag and a degenerate
        # population must refuse (a 0-item conflicts read is never a pass).
        if data.get("main_moved"):
            return emit_unknown(
                "conflicts sweep was invalidated: origin/main moved mid-sweep")
        if total < 1:
            return emit_unknown(
                f"conflicts population is empty or degenerate (total {total!r})")
        # The conflicts read is a POINT-IN-TIME snapshot of currently open PRs:
        # the API keeps no historical conflict state, so it cannot be scoped to
        # the run window. It is stamped so a reader cannot mistake it for one.
        conflicted = {
            "total": total,
            "conflicting": sum(
                1 for i in data["items"]
                if isinstance(i, dict) and i.get("conflicting")
            ),
            "unknown": sum(
                1 for i in data["items"]
                if isinstance(i, dict) and i.get("unknown")
            ),
            "window_scope": "point-in-time snapshot at capture (NOT window-scoped)",
            "verified_at": _iso(as_of or datetime.now(timezone.utc)),  # noqa: UP017
            "source": "--conflicts-json (the instrument's --json .conflicts)",
        }

    refs = live_queue_refs() if (args.live or args.confirm_refs) else None
    confirmed = len(refs) if refs is not None else None
    # Provenance: resolve origin/main only on a LIVE read (it shells out to `git
    # ls-remote`). A `--from-json` replay stays hermetic unless the caller passes
    # `--main-sha`, so an offline replay never silently depends on the network —
    # and the test suite's `--from-json` cases perform no network I/O.
    resolved_sha = args.main_sha or (resolve_origin_main() if args.live else None)
    record = build_record(
        runs, args.window_hours, confirmed_max_parallel=confirmed,
        conflicted_set=conflicted, resolved_main_sha=resolved_sha, now=as_of,
        corpus_truncated=(truncated if args.live else None),
    )
    if truncated:
        if record.get("status") == "OK":
            record["window"]["truncated"] = True
            record["window"]["truncated_note"] = (
                "the run read hit its page cap: capacity readings are a LOWER bound"
            )
        else:
            # The read hit its page cap AND the window held no usable runs. The
            # UNKNOWN body has no `window` key: stamping truncation onto it must
            # not replace a clean exit-2 UNKNOWN with an uncaught KeyError (exit
            # 1, and no record written — leaving a stale OK on disk).
            record["truncated"] = True
    text = jsonlib.dumps(record, indent=2, sort_keys=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if record.get("status") == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
