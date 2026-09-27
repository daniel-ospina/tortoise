"""Hermetic tests for `tools/queue_window_observe.py` (#5215 plan Task 3).

No network, no DB: every case drives pure functions over synthetic run dicts.
The observer emits RECORDS; the instrument owns every verdict, so these tests
pin the record's *shape and reconciliation* (the keys `check batch-size` /
`check capacity` / `check parallelism-headroom` read) and the analysis that
separates a batch FORMATION from a bisection re-run.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import queue_window_observe as obs  # noqa: E402

NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


def _iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(branch, title, minutes_ago, name="Python CI", conclusion="failure",
        wait_min=0.0, duration_min=40.0):
    created = NOW - timedelta(minutes=minutes_ago)
    started = created + timedelta(minutes=wait_min)
    updated = started + timedelta(minutes=duration_min)
    return {
        "id": abs(hash((branch, title, minutes_ago, name))) % 10**10,
        "head_branch": branch,
        "display_title": title,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": _iso(created),
        "run_started_at": _iso(started),
        "updated_at": _iso(updated),
    }


# ---------------------------------------------------------------------------
# Title parsing — the "4-PR batch" misread must not recur.
# ---------------------------------------------------------------------------

def test_parse_batch_title_separates_the_batch_from_the_stack():
    batch, stacked = obs.parse_batch_title(
        "merge queue: checking #5334 + #5315 together on main (5b6cb93), "
        "stacked on #5341 and #5307"
    )
    assert batch == [5334, 5315]
    assert stacked == [5341, 5307]


def test_parse_batch_title_single_pr():
    assert obs.parse_batch_title("merge queue: checking #5343 on main (5b6cb93)") == (
        [5343], [])


def test_parse_batch_title_unparseable_is_empty_not_a_guess():
    assert obs.parse_batch_title("some other check") == ([], [])
    assert obs.parse_batch_title(None) == ([], [])


def test_main_sha_is_read_from_the_queue_title():
    assert obs._main_sha_from_title(
        "merge queue: checking #1 on main (5b6cb9367)") == "5b6cb9367"
    assert obs._main_sha_from_title("nope") is None


# ---------------------------------------------------------------------------
# M3 — the effective value is REPEATABLE, never one max observation.
# ---------------------------------------------------------------------------

def _formations(sizes_and_offsets):
    out = []
    for i, (size, offset) in enumerate(sizes_and_offsets):
        prs = [1000 + i * 10 + k for k in range(size)]
        out.append({
            "branch": f"mergify/merge-queue/{i:02d}",
            "at": NOW - timedelta(minutes=offset),
            "batch_prs": prs, "stacked_prs": [], "size": size, "main_sha": "abc1234",
        })
    return out


def test_repeatable_wave_size_ignores_a_one_off_burst():
    # a 15-branch burst seen once must NOT become the effective value
    wave_a = _formations([(1, 500)] * 15)
    wave_b = _formations([(1, 400)] * 5)
    wave_c = _formations([(1, 300)] * 5)
    value, counts = obs.repeatable_wave_size([wave_a, wave_b, wave_c])
    assert value == 5, counts
    assert counts[15] == 1 and counts[5] == 2


def test_repeatable_wave_size_is_unknown_without_a_repeat():
    value, _ = obs.repeatable_wave_size([_formations([(1, 10)] * 3)])
    assert value is None


def test_cluster_waves_splits_on_the_formation_gap():
    # offsets are MINUTES AGO, so time order is 30, 2, 1, 0
    forms = _formations([(2, 0), (2, 1), (2, 2), (2, 30)])
    waves = obs.cluster_waves(forms, gap_seconds=360)
    assert [len(w) for w in waves] == [1, 3]


def test_sweep_max_concurrency_counts_overlaps():
    t = NOW
    intervals = [
        (t, t + timedelta(minutes=10)),
        (t + timedelta(minutes=5), t + timedelta(minutes=20)),
        (t + timedelta(minutes=7), t + timedelta(minutes=8)),
        (t + timedelta(minutes=40), t + timedelta(minutes=50)),
    ]
    best, at = obs.sweep_max_concurrency(intervals)
    assert best == 3
    assert at == t + timedelta(minutes=7)


# ---------------------------------------------------------------------------
# M5 — a single after a batch is a BISECTION, not a formation.
# ---------------------------------------------------------------------------

def test_bisection_singles_are_separated_from_formations():
    forms = _formations([(2, 60), (1, 30)])
    # the single's PR (1000) was in the earlier pair -> bisection
    forms[1]["batch_prs"] = [1000]
    bisections = obs.bisection_singles(forms)
    assert [f["branch"] for f in bisections] == [forms[1]["branch"]]


def test_a_genuinely_new_single_is_a_formation_not_a_bisection():
    forms = _formations([(2, 60), (1, 30)])
    forms[1]["batch_prs"] = [9999]
    assert obs.bisection_singles(forms) == []


def test_bisection_lookback_bound_is_respected():
    forms = _formations([(2, 10_000), (1, 30)])
    forms[1]["batch_prs"] = [1000]
    # the earlier batch is far outside the lookback -> not a bisection
    assert obs.bisection_singles(forms, lookback_seconds=3600) == []


# ---------------------------------------------------------------------------
# M6 — red wave discards its speculative siblings (measure, don't assume).
# ---------------------------------------------------------------------------

def test_red_wave_discards_wave_size_minus_one():
    waves = [_formations([(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)])]
    runs = [run(w[0]["branch"], "merge queue: checking #1 + #2 together on main (abc)",
                minutes_ago=0, conclusion="failure") for w in waves]
    reds = obs.red_waves(waves, runs)
    assert reds[0]["red"] is True
    assert reds[0]["discarded_speculative"] == 4


def test_green_wave_discards_nothing():
    waves = [_formations([(2, 0), (2, 1)])]
    runs = [run(waves[0][0]["branch"], "merge queue: checking #1 + #2 together on main (abc)",
                minutes_ago=0, conclusion="success")]
    reds = obs.red_waves(waves, runs)
    assert reds[0]["red"] is False
    assert reds[0]["discarded_speculative"] == 0


# ---------------------------------------------------------------------------
# M4 — capacity: a stale wait yields UNKNOWN, never a guessed number.
# ---------------------------------------------------------------------------

def test_oldest_queued_minutes_tracks_the_oldest_waiting_run():
    oldest, at = obs.oldest_queued_minutes(
        [(NOW - timedelta(minutes=90), NOW + timedelta(minutes=1)),
         (NOW - timedelta(minutes=5), NOW + timedelta(minutes=1))],
        NOW, step_minutes=5,
    )
    assert oldest == pytest.approx(90, abs=5)
    assert at is not None


def test_capacity_at_first_failure_is_unknown_on_a_stale_wait():
    # the shape the live window showed: one startup_failure, queued ~40 h
    runs = [
        # a queue branch so the record is built at all
        run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=3100, name="Python CI", conclusion="failure",
            wait_min=0.0, duration_min=40.0),
        run("feat/x", "merge queue: checking #1 on main (abc)", minutes_ago=3000,
            name="Python CI", conclusion="startup_failure", wait_min=2380,
            duration_min=0.0),
    ]
    record = obs.build_record(runs, window_hours=24 * 40, now=NOW)
    cap = record["capacity"]
    assert cap["capacity_at_first_failure"] == obs.UNKNOWN
    assert "stale bound" in cap["capacity_at_first_failure_reason"]


def test_capacity_at_first_failure_is_read_when_the_wait_is_localizable():
    base = run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
               minutes_ago=200, conclusion="failure", wait_min=0.0, duration_min=40.0)
    # a run that NEVER held a runner, with a localizable wait
    starving = run("feat/y", "merge queue: checking #9 on main (abc)", minutes_ago=180,
                   name="CI", conclusion="startup_failure", wait_min=90.0,
                   duration_min=40.0)
    record = obs.build_record([base, starving], window_hours=24, now=NOW)
    cap = record["capacity"]
    assert cap["capacity_at_first_failure"] != obs.UNKNOWN
    assert isinstance(cap["capacity_at_first_failure"], int)
    # I9's unit trap: the runs reading and the batch reading are both recorded,
    # because `configured_max_parallel_checks` is in BATCHES, not runs.
    assert "capacity_at_first_failure_batches" in cap
    assert cap["capacity_at_first_failure_batches"] >= 1


def test_a_delayed_run_is_not_a_runner_acquisition_failure():
    # a run that waited 90 min and then RAN was delayed, not starved: it must
    # not manufacture a capacity number out of ordinary queue pressure.
    runs = [
        run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=200, conclusion="failure", wait_min=0.0, duration_min=40.0),
        run("feat/z", "merge queue: checking #8 on main (abc)", minutes_ago=180,
            name="CI", conclusion="success", wait_min=90.0, duration_min=40.0),
    ]
    record = obs.build_record(runs, window_hours=24, now=NOW)
    cap = record["capacity"]
    assert cap["capacity_at_first_failure"] == obs.UNKNOWN
    assert cap["runs_delayed_over_60min"] == 1
    assert cap["max_queue_wait_minutes"] == pytest.approx(90, abs=1)


def test_capacity_unknown_when_nothing_failed_to_start():
    runs = [run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
                minutes_ago=100)]
    record = obs.build_record(runs, window_hours=24, now=NOW)
    assert record["capacity"]["capacity_at_first_failure"] == obs.UNKNOWN


# ---------------------------------------------------------------------------
# Record shape — the keys the instrument's checks read.
# ---------------------------------------------------------------------------

def _corpus():
    runs = []
    # two identical 5-branch waves (so the effective value is repeatable), then
    # a bisection single per red wave
    for wave_i, offset in enumerate((600, 300)):
        for k in range(5):
            branch = f"mergify/merge-queue/w{wave_i}b{k}"
            runs.append(run(branch,
                            f"merge queue: checking #{2000 + k * 2} + #{2001 + k * 2} "
                            "together on main (5b6cb93)",
                            minutes_ago=offset + k, conclusion="failure"))
        runs.append(run(f"mergify/merge-queue/w{wave_i}bi",
                        f"merge queue: checking #{2000 + wave_i} on main (5b6cb93)",
                        minutes_ago=offset - 20, conclusion="failure"))
    return runs


def test_record_effective_parallelism_is_the_repeatable_wave_size():
    record = obs.build_record(_corpus(), window_hours=24, now=NOW)
    assert record["parallelism"]["effective_max_parallel_checks"] == 5
    assert record["parallelism"]["max_observed_batches"] == 5


def test_record_batch_sizes_exclude_bisections_and_reconcile():
    record = obs.build_record(_corpus(), window_hours=24, now=NOW)
    batches = record["batches"]
    assert batches["events"] == len(batches["batch_sizes"])
    assert batches["max_batch_size"] == max(batches["batch_sizes"])
    assert batches["max_batch_size"] == 2
    assert batches["bisection_singles"] >= 1
    assert set(batches) >= {"events", "batch_sizes", "max_batch_size", "verified_at"}


def test_record_has_every_capacity_field_the_instrument_requires():
    record = obs.build_record(_corpus(), window_hours=24, now=NOW)
    cap = record["capacity"]
    for key in ("queued", "in_progress", "oldest_minutes",
                "capacity_at_first_failure", "configured_max_parallel_checks"):
        assert key in cap, key
    for key in ("queued", "in_progress", "oldest_minutes",
                "capacity_at_first_failure"):
        assert key in cap["records"], key
        assert cap["records"][key]["verified_at"]


def test_record_counts_the_red_wave_invalidations():
    record = obs.build_record(_corpus(), window_hours=24, now=NOW)
    inv = record["invalidations"]
    # 4 waves: two 5-branch batch waves + two bisection singles (size 1)
    assert inv["waves"] == 4
    assert inv["batch_waves"] == 2
    assert inv["red_waves"] == 4
    assert inv["discarded_speculative"] == 8  # two batch waves x (5-1)
    assert inv["waste_ratio"] == pytest.approx(4.0)


def test_bisection_wave_discards_no_speculation():
    record = obs.build_record(_corpus(), window_hours=24, now=NOW)
    singles = [w for w in record["invalidations"]["per_wave"] if w["wave_size"] == 1]
    assert singles
    assert all(w["discarded_speculative"] == 0 for w in singles)


def test_record_refuses_an_empty_enumeration_as_unknown_not_zero():
    record = obs.build_record([], window_hours=24, now=NOW)
    assert record["status"] == obs.UNKNOWN
    assert "reason" in record


def test_record_reports_configured_value_and_its_source():
    record = obs.build_record(_corpus(), window_hours=24, now=NOW)
    assert record["parallelism"]["configured_max_parallel_checks"] == 5
    assert "max_parallel_checks" not in record["parallelism"]["configured_source"]


def test_configured_max_parallel_checks_reads_a_real_setting(tmp_path):
    cfg = tmp_path / "mergify.yml"
    cfg.write_text("queue_rules:\n  - name: main\n    max_parallel_checks: 3\n")
    value, source = obs.configured_max_parallel_checks(cfg)
    assert value == 3 and source == "config"


def test_configured_max_parallel_checks_falls_back_to_the_documented_default(
        tmp_path):
    cfg = tmp_path / "mergify.yml"
    cfg.write_text("queue_rules:\n  - name: main\n    batch_size: 2\n")
    value, source = obs.configured_max_parallel_checks(cfg)
    assert value == obs.DEFAULT_MAX_PARALLEL_CHECKS
    assert source.startswith("documented-default")


# ---------------------------------------------------------------------------
# CLI contract — empty is exit 2, and the record is JSON.
# ---------------------------------------------------------------------------

def test_cli_exits_2_on_an_empty_read(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("")
    assert obs.main(["--from-json", str(runs), "--window-hours", "8"]) == 2


def test_cli_writes_a_json_record(tmp_path, capsys):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("\n".join(json.dumps(r) for r in _corpus()))
    out = tmp_path / "record.json"
    assert obs.main(["--from-json", str(runs), "--window-hours", "24",
                     "--out", str(out)]) == 0
    record = json.loads(out.read_text())
    assert record["status"] == "OK"
    assert record["parallelism"]["effective_max_parallel_checks"] == 5
