"""#2292 Task 7 — thresholds re-locks + R2 gate-form + metric-key + determinism."""
from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401

from battery.config.thresholds import ThresholdsConfig, load_thresholds
from battery.report.calibrate import cal_table_hash

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"


def _rows(cfg) -> dict:
    return {(m, a): v for m, a, v in cfg.cal_rows}


def test_r2_gate_form_consistent_ratio_ge_1_5():
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    a4 = rows[("coverage-subscore", "a4")]
    a0 = rows[("coverage-subscore", "a0")]
    if a0 == 0.0:
        assert a4 > 0.0                      # a0=0 floor: pass iff treatment > 0
    else:
        assert a4 >= 1.5 * a0                # canonical form — no 1.43x tolerated


def test_flip_flop_and_fp_rows_present_placeholder_locked():
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    metrics = {m for m, _ in rows}
    assert {"flip-flop-rate", "false-positive-rate"} <= metrics
    # round-2: both rows are authored BEFORE their measurement path exists
    # (bct control-verdict emission + the rate pools are #2284 Task 9
    # executor-owned) — they must be PLACEHOLDER-LOCKED with a stated basis
    # (the AC nominal upper-bound caps as placeholders + the bct-pooled fp
    # denominator), never a stale-guess-as-locked measured claim. The row
    # VALUES trace to that basis (the caps), and the lock annotation lives
    # in the thresholds.yaml provenance comments next to the rows.
    assert rows[("flip-flop-rate", "a4")] == 0.10       # AC cap placeholder
    assert rows[("false-positive-rate", "a4")] == 0.05  # AC cap placeholder
    raw = (CONFIG / "thresholds.yaml").read_text(encoding="utf-8")
    assert raw.count("placeholder-locked") >= 2


def test_surfaced_rate_row_post_i1_semantics():
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    a4 = rows[("surfaced-rate", "a4")]
    a0 = rows[("surfaced-rate", "a0")]
    # round-2: the OLD assert (a0 == 0.0 only) was VACUOUS — it already held
    # against the pre-fix contradiction row {a4: 0.90, a0: 0.00} (the
    # no-store floor never moves), so it could not DETECT the post-I-1
    # correction. The discriminating meaning is the A4 expectation's
    # delta-direction/decidability: post-I-1 seed-mode semantics the row is
    # the mock-lane MEASURED basis (04-plan fixture matrix A4 0.92 — the
    # planted-population surfaced rate under the seed-mode split), strictly
    # ABOVE the no-store comparator AND strictly above the verbatim E2E-1.1
    # gate nominal — a row copied verbatim from the 90% AC number is an
    # undecidable boundary lock.
    assert a0 == 0.0        # no-store arm comparator intact (floor)
    assert a4 > a0          # delta-direction: treatment > control
    assert a4 > 0.90        # seed-mode measured basis above the 90% AC
    #                         nominal with margin — never the verbatim copy


def test_ep_variance_row_intact_owned_by_2291():
    # n9: the [cal] ep-variance row is #2291-OWNED — #2292 references it in
    # provenance, never edits it. Value stays the product per-call default.
    cfg = load_thresholds(CONFIG / "thresholds.yaml")
    rows = _rows(cfg)
    assert rows[("ep-variance", "a4")] == 0.04


def test_coverage_metric_key_canonical_hyphen():
    # the report-visible probe metric + the cal-table key share ONE hyphen
    # spelling (underscore retired from report-visible surfaces; the
    # schema-v1.1 event-log field coverage_subscore is log-internal).
    from battery.probes.r2_coverage import R2CoverageProbe
    assert R2CoverageProbe.metric == R2CoverageProbe.cal_metric \
        == "coverage-subscore"


def test_delta_vs_control_a0_zero_floor():
    from battery.probes.r2_coverage import R2CoverageProbe
    p = R2CoverageProbe()
    assert p.delta_vs_control(0.5, 0.0) == 1.0      # control 0 + treatment>0
    assert p.delta_vs_control(0.0, 0.0) == 0.0      # control 0 + treatment 0
    assert p.delta_vs_control(0.5, 0.25) == 2.0     # plain ratio otherwise


def test_determinism_relock_hash_roundtrip():
    t1 = load_thresholds(CONFIG / "thresholds.yaml")
    assert t1.cal_table_hash() == cal_table_hash(
        t1.cal_rows, t1.determinism_tolerances)
    # the probe-usage row is folded into the hash — dropping it drifts
    dropped = ThresholdsConfig(cal_rows=t1.cal_rows)
    assert dropped.cal_table_hash() != t1.cal_table_hash()
    tol = dict(t1.determinism_tolerances)
    assert "probe_usage_tokens_episode" in tol
    assert tol["probe_usage_tokens_episode"] > 0.0  # provisional real row
