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
        wait_min=0.0, duration_min=40.0, status="completed", attempt=1):
    created = NOW - timedelta(minutes=minutes_ago)
    started = created + timedelta(minutes=wait_min)
    updated = started + timedelta(minutes=duration_min)
    return {
        "id": abs(hash((branch, title, minutes_ago, name, attempt))) % 10**10,
        "head_branch": branch,
        "display_title": title,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "created_at": _iso(created),
        "run_started_at": _iso(started),
        "updated_at": _iso(updated),
    }


def queued_only(branch, minutes_ago):
    """A run still waiting: no `run_started_at`, `status: queued`."""
    return {
        "id": abs(hash((branch, minutes_ago))) % 10**10,
        "head_branch": branch,
        "display_title": "merge queue: checking #1 on main (abc1234)",
        "name": "Python CI",
        "status": "queued",
        "conclusion": None,
        "created_at": _iso(NOW - timedelta(minutes=minutes_ago)),
        "run_started_at": None,
        "updated_at": _iso(NOW - timedelta(minutes=minutes_ago)),
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


def test_ts_is_none_on_garbage():
    assert obs._ts("garbage") is None
    assert obs._ts(None) is None
    assert obs._ts("2026-09-27T09:00:00Z") == NOW


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


def test_sweep_max_concurrency_of_nothing_is_unknown_not_zero():
    # an empty read must never read as "nothing queued, nothing running"
    assert obs.sweep_max_concurrency([]) == (None, None)


def test_oldest_queued_minutes_of_nothing_is_unknown():
    assert obs.oldest_queued_minutes([], NOW) == (None, None)


def test_oldest_queued_minutes_is_the_exact_max_not_a_grid_sample():
    intervals = [
        (NOW - timedelta(minutes=90), NOW - timedelta(minutes=3)),
        (NOW - timedelta(minutes=5), NOW),
    ]
    oldest, at = obs.oldest_queued_minutes(intervals, NOW)
    assert oldest == pytest.approx(87, abs=0.01)
    assert at == NOW - timedelta(minutes=3)


# ---------------------------------------------------------------------------
# M4 — a queued run with no start time is VISIBLE, not absent.
# ---------------------------------------------------------------------------

def test_queue_intervals_include_a_still_queued_run():
    intervals = obs.queue_intervals([queued_only("feat/jam", 600)], NOW)
    assert intervals, "a still-queued run must not vanish from the queue read"
    oldest, _ = obs.oldest_queued_minutes(intervals, NOW)
    assert oldest == pytest.approx(600, abs=1)


def test_zero_length_queue_wait_is_dropped_not_counted_as_active():
    r = run("mergify/merge-queue/aa", "merge queue: checking #1 on main (abc)",
            minutes_ago=10, wait_min=0.0, duration_min=20.0)
    assert obs.queue_intervals([r], NOW) == []


def test_running_intervals_close_an_in_progress_run_at_now():
    r = run("mergify/merge-queue/aa", "merge queue: checking #1 on main (abc)",
            minutes_ago=30, duration_min=5.0, status="in_progress", conclusion=None)
    intervals = obs.running_intervals([r], NOW)
    assert len(intervals) == 1
    assert intervals[0][1] == NOW


# ---------------------------------------------------------------------------
# M5 — a single after a BATCH is a bisection; a repeat single is not.
# ---------------------------------------------------------------------------

def test_bisection_singles_are_separated_from_formations():
    forms = _formations([(2, 60), (1, 30)])
    forms[1]["batch_prs"] = [1000]  # the pair's first PR
    bisections = obs.bisection_singles(forms)
    assert [f["branch"] for f in bisections] == [forms[1]["branch"]]


def test_a_genuinely_new_single_is_a_formation_not_a_bisection():
    forms = _formations([(2, 60), (1, 30)])
    forms[1]["batch_prs"] = [9999]
    assert obs.bisection_singles(forms) == []


def test_a_repeat_single_is_not_a_bisection():
    # the prior formation is a single, not a batch: a split needs a BATCH parent
    forms = _formations([(1, 90), (1, 30)])
    forms[1]["batch_prs"] = forms[0]["batch_prs"]
    assert obs.bisection_singles(forms) == []


def test_a_split_older_than_two_hours_is_still_a_bisection():
    # unbounded lookback: PR 4617 forms alone twice, 3 h 05 apart in the data
    forms = _formations([(2, 400), (1, 200)])
    forms[1]["batch_prs"] = [forms[0]["batch_prs"][0]]
    assert len(obs.bisection_singles(forms)) == 1


# ---------------------------------------------------------------------------
# M6 — red wave discards its speculative siblings (measure, don't assume).
# ---------------------------------------------------------------------------

def test_red_wave_discards_wave_size_minus_one():
    forms = _formations([(2, 0), (2, 1), (2, 2), (2, 3), (2, 4)])
    waves = obs.cluster_waves(forms)
    head = waves[0][0]["branch"]  # the EARLIEST branch is the wave head
    runs = [run(head, "merge queue: checking #1 + #2 together on main (abc)",
                minutes_ago=0, conclusion="failure")]
    reds = obs.red_waves(waves, runs)
    assert reds[0]["red"] is True
    assert reds[0]["discarded_speculative"] == 4


def test_unobserved_wave_head_is_unknown_not_zero_waste():
    forms = _formations([(2, 0), (2, 1), (2, 2)])
    waves = obs.cluster_waves(forms)
    reds = obs.red_waves(waves, [])  # no heavy-leg run observed for the head
    assert reds[0]["red"] is None
    assert reds[0]["discarded_speculative"] == 0  # a floor, NOT "verified clean"


def test_a_rerun_green_wave_head_is_not_red():
    forms = _formations([(2, 0), (2, 1)])
    waves = obs.cluster_waves(forms)
    head = waves[0][0]["branch"]
    runs = [
        run(head, "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=5, conclusion="failure", attempt=1),
        run(head, "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=1, conclusion="success", attempt=2),
    ]
    reds = obs.red_waves(waves, runs)
    assert reds[0]["red"] is False
    assert reds[0]["discarded_speculative"] == 0


# ---------------------------------------------------------------------------
# M4 — capacity: a stale or unlocalizable wait yields UNKNOWN, never a guess.
# ---------------------------------------------------------------------------

def _queue_run(branch, title, minutes_ago, **kw):
    return run(branch, title, minutes_ago, **kw)


def test_capacity_at_first_failure_is_unknown_on_a_stale_wait():
    runs = [
        run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=3100, conclusion="failure", duration_min=40.0),
        # the live shape: one startup_failure, queued ~40 h
        run("feat/x", "merge queue: checking #1 on main (abc)", minutes_ago=3000,
            name="Python CI", conclusion="startup_failure", wait_min=2380,
            duration_min=0.0),
    ]
    record = obs.build_record(runs, window_hours=24 * 40, now=NOW)
    cap = record["capacity"]
    assert cap["capacity_at_first_failure"] == obs.UNKNOWN
    assert "stale bound" in cap["capacity_at_first_failure_reason"]


def test_a_stale_candidate_does_not_discard_a_localizable_later_one():
    runs = [
        run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=900, conclusion="failure", duration_min=40.0),
        run("feat/stale", "merge queue: checking #7 on main (abc)", minutes_ago=800,
            conclusion="startup_failure", wait_min=700, duration_min=0.0),
        run("feat/local", "merge queue: checking #8 on main (abc)", minutes_ago=200,
            conclusion="startup_failure", wait_min=30, duration_min=0.0),
    ]
    record = obs.build_record(runs, window_hours=24, now=NOW)
    cap = record["capacity"]
    assert cap["capacity_at_first_failure"] != obs.UNKNOWN
    assert "2 acquisition-failure candidate(s)" in cap["capacity_at_first_failure_reason"]


def test_a_candidate_with_no_start_time_does_not_crash():
    # a startup_failure frequently has no run_started_at; formatting it must not
    # raise (this was a live TypeError before the fix)
    r = {
        "head_branch": "feat/y", "display_title": "merge queue: checking #9 on main (abc)",
        "name": "Python CI", "status": "completed", "conclusion": "startup_failure",
        "created_at": _iso(NOW - timedelta(minutes=100)), "run_started_at": None,
        "updated_at": _iso(NOW - timedelta(minutes=100)),
    }
    record = obs.build_record([r], window_hours=24, now=NOW)
    assert record["status"] == obs.UNKNOWN or (
        record["capacity"]["capacity_at_first_failure"] == obs.UNKNOWN)


def test_a_delayed_run_is_not_a_runner_acquisition_failure():
    runs = [
        run("mergify/merge-queue/aa", "merge queue: checking #1 + #2 together on main (abc)",
            minutes_ago=200, conclusion="failure", duration_min=40.0),
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


MERGIFY_NO_MAX = REPO / "tests" / "fixtures" / "mergify-no-max-parallel.yml"


def _build(runs, **kw):
    # Hermetic: pin the config reader to a fixture with no `max_parallel_checks`,
    # so the record does not depend on the repo's live `.mergify.yml` (PR #5527
    # would set `max_parallel_checks: 3` there and silently change this record).
    kw.setdefault("config_path", MERGIFY_NO_MAX)
    return obs.build_record(runs, kw.pop("window_hours", 24), now=NOW, **kw)


def test_record_effective_parallelism_is_the_repeatable_wave_size():
    record = _build(_corpus())
    assert record["parallelism"]["effective_max_parallel_checks"] == 5
    assert record["parallelism"]["max_observed_batches"] == 5


def test_record_effective_parallelism_ignores_a_one_off_larger_wave():
    # DISCRIMINATING: a "max observed" implementation would return 7 here
    runs = []
    for wave_i, offset in enumerate((900, 600, 300)):
        size = 7 if wave_i == 0 else 5
        for k in range(size):
            branch = f"mergify/merge-queue/x{wave_i}b{k}"
            runs.append(run(branch,
                            f"merge queue: checking #{3000 + k * 2} + #{3001 + k * 2} "
                            "together on main (5b6cb93)",
                            minutes_ago=offset + k, conclusion="failure"))
    record = _build(runs)
    assert record["parallelism"]["max_observed_batches"] == 7
    assert record["parallelism"]["effective_max_parallel_checks"] == 5


def test_record_batch_sizes_exclude_bisections_and_reconcile():
    record = _build(_corpus())
    batches = record["batches"]
    assert batches["events"] == len(batches["batch_sizes"]) == 10
    assert batches["max_batch_size"] == max(batches["batch_sizes"])
    assert batches["max_batch_size"] == 2
    assert batches["bisection_singles"] == 2
    assert set(batches) >= {"events", "batch_sizes", "max_batch_size", "verified_at"}


def test_record_has_every_capacity_field_the_instrument_requires():
    record = _build(_corpus())
    cap = record["capacity"]
    for key in ("queued", "in_progress", "oldest_minutes",
                "capacity_at_first_failure", "configured_max_parallel_checks",
                "headroom"):
        assert key in cap, key
    for key in ("queued", "in_progress", "oldest_minutes",
                "capacity_at_first_failure"):
        assert key in cap["records"], key
        assert cap["records"][key]["verified_at"]


def test_headroom_is_unknown_when_the_capacity_sample_is_unknown():
    record = _build(_corpus())
    assert record["capacity"]["headroom"] == obs.UNKNOWN


def test_record_counts_the_red_wave_invalidations():
    record = _build(_corpus())
    inv = record["invalidations"]
    # bisections are excluded from waves, so there are two 5-branch waves
    assert inv["waves"] == 2
    assert inv["batch_waves"] == 2
    assert inv["red_waves"] == 2
    assert inv["discarded_speculative"] == 8  # two waves x (5-1)
    assert inv["waste_ratio"] == pytest.approx(4.0)


def test_record_flags_an_unobserved_wave_head():
    # keep the queue branches but rename the leg, so no head outcome is observed
    runs = [dict(r, name="CI") for r in _corpus()]
    record = _build(runs)
    assert record["invalidations"]["unobserved_waves"] >= 1
    assert record["invalidations"]["red_waves"] == 0
    assert record["invalidations"]["discarded_speculative"] == 0


def test_record_refuses_an_empty_enumeration_as_unknown_not_zero():
    record = obs.build_record([], window_hours=24, now=NOW)
    assert record["status"] == obs.UNKNOWN
    assert "reason" in record


def test_record_refuses_a_window_with_no_runs_instead_of_widening():
    # the corpus is 5 days old but an 8 h window is asked for: reporting the old
    # data under the requested window is the fail-open shape we must avoid
    old = [run("mergify/merge-queue/aa",
               "merge queue: checking #1 + #2 together on main (abc)",
               minutes_ago=5 * 24 * 60, conclusion="failure", duration_min=40.0)]
    record = obs.build_record(old, window_hours=8, now=NOW)
    assert record["status"] == obs.UNKNOWN
    assert "inside the 8" in record["reason"]


def test_record_reports_configured_value_and_its_source():
    record = _build(_corpus())
    assert record["parallelism"]["configured_max_parallel_checks"] == 5
    assert "max_parallel_checks" not in record["parallelism"]["configured_source"]


def test_basis_is_honest_when_the_effective_value_is_unknown():
    runs = [run("mergify/merge-queue/aa",
                "merge queue: checking #1 + #2 together on main (abc)",
                minutes_ago=100, conclusion="failure", duration_min=40.0)]
    record = _build(runs)
    assert record["parallelism"]["effective_max_parallel_checks"] == obs.UNKNOWN
    assert "no repeatable wave" in record["parallelism"]["basis"]


def test_configured_max_parallel_checks_reads_a_real_setting(tmp_path):
    cfg = tmp_path / "mergify.yml"
    cfg.write_text("queue_rules:\n  - name: main\n    max_parallel_checks: 3\n")
    value, source = obs.configured_max_parallel_checks(cfg)
    assert value == 3 and source == "config"


def test_configured_max_parallel_checks_tolerates_a_trailing_comment(tmp_path):
    cfg = tmp_path / "mergify.yml"
    cfg.write_text("    max_parallel_checks: 3  # a deliberate cap\n")
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
# CLI contract — empty/failed is exit 2, and the record is JSON.
# ---------------------------------------------------------------------------

def test_cli_exits_2_on_an_empty_read(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("")
    assert obs.main(["--from-json", str(runs), "--window-hours", "8"]) == 2


def test_cli_exits_2_on_an_unparseable_line(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text('{"head_branch": "x"}\n{not json}\n')
    assert obs.main(["--from-json", str(runs), "--window-hours", "8"]) == 2


def test_cli_overwrites_a_stale_out_file_with_unknown(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("")
    out = tmp_path / "record.json"
    out.write_text('{"status": "OK", "parallelism": {"effective_max_parallel_checks": 5}}')
    assert obs.main(["--from-json", str(runs), "--window-hours", "8",
                     "--out", str(out)]) == 2
    assert json.loads(out.read_text())["status"] == obs.UNKNOWN


def test_cli_writes_a_json_record(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("\n".join(json.dumps(r) for r in _corpus()))
    out = tmp_path / "record.json"
    assert obs.main(["--from-json", str(runs), "--window-hours", "24",
                     "--out", str(out)]) == 0
    record = json.loads(out.read_text())
    assert record["status"] == "OK"
    assert record["parallelism"]["effective_max_parallel_checks"] == 5


def test_cli_exits_2_on_an_unreadable_conflicts_input(tmp_path):
    # A REQUESTED conflicts read that cannot be read is UNKNOWN (exit 2), never
    # `conflicted_set: null` at exit 0 (which reads as "checked, none found").
    runs = tmp_path / "runs.jsonl"
    runs.write_text("\n".join(json.dumps(r) for r in _corpus()))
    bad = tmp_path / "conflicts.json"
    bad.write_text("{not json")
    out = tmp_path / "record.json"
    assert obs.main(["--from-json", str(runs), "--window-hours", "24",
                     "--conflicts-json", str(bad), "--out", str(out)]) == 2
    assert json.loads(out.read_text())["status"] == obs.UNKNOWN


def test_as_of_pins_the_window_end(tmp_path):
    # `--as-of` makes a replay reproducible AND keeps the window end inside the
    # corpus's coverage, so runs created after the dump was taken cannot read as
    # absent demand.
    runs = tmp_path / "runs.jsonl"
    runs.write_text("\n".join(json.dumps(r) for r in _corpus()))
    out = tmp_path / "record.json"
    assert obs.main(["--from-json", str(runs), "--window-hours", "24",
                     "--as-of", _iso(NOW), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["window"]["end"] == _iso(NOW)


def test_from_json_replay_does_not_shell_out_for_a_main_sha(tmp_path, monkeypatch):
    # `--from-json` must stay hermetic: resolving origin/main shells out to `git
    # ls-remote`, so it is only done on `--live` (or when `--main-sha` is given).
    def _boom(*_a, **_k):
        raise AssertionError("resolve_origin_main must not run on a --from-json replay")

    monkeypatch.setattr(obs, "resolve_origin_main", _boom)
    runs = tmp_path / "runs.jsonl"
    runs.write_text("\n".join(json.dumps(r) for r in _corpus()))
    out = tmp_path / "record.json"
    assert obs.main(["--from-json", str(runs), "--window-hours", "24",
                     "--out", str(out)]) == 0
    record = json.loads(out.read_text())
    assert record["window"]["main_sha_source"].startswith("unresolved")


def test_cli_truncated_unknown_is_exit_2_not_a_crash(tmp_path, monkeypatch):
    # A capped read whose window holds no queue runs returns an UNKNOWN body with
    # no `window` key. Stamping truncation must not raise KeyError (exit 1, no
    # record written, stale --out left on disk) — it must be a clean exit-2
    # UNKNOWN that is still written.
    runs = [run("feature/x", "unrelated push run", minutes_ago=5)]
    monkeypatch.setattr(obs, "fetch_runs", lambda pages=8: (runs, True))
    monkeypatch.setattr(obs, "live_queue_refs", lambda: None)
    monkeypatch.setattr(obs, "resolve_origin_main", lambda: "0" * 40)
    out = tmp_path / "record.json"
    code = obs.main(["--live", "--window-hours", "8", "--out", str(out)])
    assert code == 2
    body = json.loads(out.read_text())
    assert body["status"] == obs.UNKNOWN
    assert body["truncated"] is True


def test_queue_intervals_exclude_a_completed_run_with_no_start():
    # `startup_failure` is completed with no `run_started_at` but NEVER queued.
    # Counting it as "queued until now" inflates queued/oldest/capacity (fail-OPEN).
    never_queued = [{
        "head_branch": "feature/x", "name": "Python CI", "status": "completed",
        "conclusion": "startup_failure",
        "created_at": _iso(NOW - timedelta(hours=40)),
        "run_started_at": None, "updated_at": _iso(NOW - timedelta(hours=40)),
    }]
    assert obs.queue_intervals(never_queued, now=NOW) == []


def test_record_reports_queue_depth_at_each_formation():
    # M5: the queue depth AT FORMATION, not a momentary sample elsewhere.
    record = _build(_corpus())
    formed = [f for f in record["batches"]["formations"] if not f["bisection"]]
    assert formed
    assert all(f["queue_depth_at_formation"] >= 1 for f in formed)
    assert record["batches"]["max_queue_depth_at_formation"] >= 1


# ---------------------------------------------------------------------------
# The committed evidence must agree with the doc that cites it. A reviewer
# caught the doc's M5/M6 numbers drifting from the JSON it referenced, so the
# correspondence is pinned here rather than re-checked by eye.
# ---------------------------------------------------------------------------

DOC = REPO / "docs" / "ci" / "merge-throughput-measurements.md"
M3_M6 = REPO / "docs" / "ci" / "merge-throughput-m3-m6-records.json"
M4_CAP = REPO / "docs" / "ci" / "merge-throughput-m4-capacity-records.json"


def test_committed_records_match_the_doc():
    # A MISSING artifact must fail, not skip: skipping here makes the "doc cannot
    # drift from the records" pin vacuous exactly when a record is deleted.
    assert DOC.exists(), f"missing {DOC}"
    assert M3_M6.exists(), f"missing {M3_M6}"
    assert M4_CAP.exists(), f"missing {M4_CAP}"
    doc = DOC.read_text()
    short = json.loads(M3_M6.read_text())
    long_ = json.loads(M4_CAP.read_text())

    # headline values, read from the records and asserted present in the doc
    eff = short["parallelism"]["effective_max_parallel_checks"]
    assert (str(eff), str(long_["parallelism"]["effective_max_parallel_checks"])) == ("5", "5")
    assert "**5**" in doc

    # M5: the fresh window is all pairs; the 14 d window keeps its singles
    fresh_sizes = set(short["batches"]["batch_sizes"])
    assert fresh_sizes == {2}, fresh_sizes
    assert short["batches"]["bisection_singles"] == 6
    dist_long = long_["parallelism"]["formation_size_distribution"]
    assert dist_long == {"1": 10, "2": 155}, dist_long
    assert "`{1: 10, 2: 155}`" in doc
    assert "`{2: 43}`" in doc

    # M5: queue depth at formation is windowed and recorded per formation, and
    # the conflicted set is stamped as a NON-windowed snapshot (F10)
    assert short["batches"]["max_queue_depth_at_formation"] >= 1
    assert all(f["queue_depth_at_formation"] >= 1
               for f in short["batches"]["formations"])
    assert short["batches"]["conflicted_set"]["window_scope"].startswith("point-in-time")

    # M6: discarded speculative batches
    assert short["invalidations"]["discarded_speculative"] == 17
    assert long_["invalidations"]["discarded_speculative"] == 73
    assert "**17**" in doc and "**73**" in doc
    assert short["invalidations"]["unobserved_waves"] == 1

    # M4: capacity is UNKNOWN in both windows, and headroom is never invented
    for rec in (short, long_):
        assert rec["capacity"]["capacity_at_first_failure"] == obs.UNKNOWN
        assert rec["capacity"]["headroom"] == obs.UNKNOWN
        assert rec["capacity"]["capacity_at_first_failure"] is not None

    # the window's provenance is recorded, never inferred
    for rec in (short, long_):
        assert len(rec["window"]["main_sha"]) == 40
        assert rec["window"]["main_sha_source"]
