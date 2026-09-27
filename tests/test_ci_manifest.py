"""Unit tests for the regenerable, value-validated selection manifest (#5050).

The substrate under test is `tools/ci_manifest.py`: the sweep that derives the
`durations` map from measured junit artifacts, the measurement record that is
its single source of truth, and the value / partition / guard-reachability
checks that `tools/ci_selection.py --integrity` delegates to.

Every assertion here pins a defect class from the #5050 root, not an
implementation detail:

* a wrong value, a missing row, a dead key           -> value_issues
* a classified file in no leg / two legs             -> partition_issues (#4835)
* a guard whose file selects no surface              -> guard_reachability (#3362/#4115/#4658)
* a newly registered file with no measurement        -> register_provisional (#4348/#4364)
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_manifest as cm  # noqa: I001
from tools import ci_selection as cs


# ── fixtures ─────────────────────────────────────────────────────────────


def _manifest() -> dict:
    return {
        "version": 1,
        "surfaces": {
            "core": ["test_a.py", "test_slow.py", "test_carve.py",
                     "test_both.py"],
            "other": ["test_b.py"],
        },
        "tier1": ["test_a.py"],
        "slow_files": ["test_slow.py", "test_both.py"],
        "carve_out": ["test_carve.py", "test_both.py"],
        "push_extra": [],
        "durations": {},
    }


def _junit(directory: Path, artifact: str, entries) -> Path:
    d = directory / artifact
    d.mkdir(parents=True, exist_ok=True)
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for file_, time in entries:
        ET.SubElement(suite, "testcase", file=file_, time=str(time))
    path = d / "junit.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    return path


def _source(*, a=(), b=(), slow=(), carve=()) -> dict:
    return {
        "fast": {cm._bare(f): t for f, t in (*a, *b)},
        "slow": {cm._bare(f): t for f, t in slow},
        "carve_out": {cm._bare(f): t for f, t in carve},
    }


def _record_and_manifest():
    """A consistent (manifest, record) pair from one synthetic sweep."""
    m = _manifest()
    source = _source(
        a=[("tests/test_a.py", 1.23)],
        b=[("tests/test_b.py", 0.5)],
        slow=[("tests/test_slow.py", 10.0)],
        carve=[("tests/test_carve.py", 20.0), ("tests/test_both.py", 5.0)],
    )
    record = cm.sweep([("r1", source)], m, cm.seed_from_manifest(m))
    m["durations"] = {n: row["value"] for n, row in record["rows"].items()}
    return m, record


# ── the sweep: carrying-leg rule, max-across-runs, floors ─────────────────


def test_parse_junit_sums_per_file():
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    ET.SubElement(suite, "testcase", file="tests/test_x.py", time="1.5")
    ET.SubElement(suite, "testcase", file="tests/test_x.py", time="2.5")
    ET.SubElement(suite, "testcase", file="tests/sub/test_y.py", time="0.25")
    tmp = Path(pytest.importorskip("tempfile").mkdtemp())
    path = tmp / "junit.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    assert cm.parse_junit(path) == {"test_x.py": 4.0, "sub/test_y.py": 0.25}


def test_carrying_leg_prefers_carve_out_over_slow():
    # test_both.py is a dual slow+carve file; it RUNS in the carve-out job, so
    # its weight must come from the carve-out artifact, not the slow legs.
    m = _manifest()
    fast = set(cs.fast_pool(m))
    slow = set(m["slow_files"])
    carve = cs.carve_out_files(m)
    assert cm.carrying_leg("test_both.py", fast, slow, carve) == "carve_out"
    assert cm.carrying_leg("test_a.py", fast, slow, carve) == "fast"
    assert cm.carrying_leg("test_slow.py", fast, slow, carve) == "slow"
    assert cm.carrying_leg("test_carve.py", fast, slow, carve) == "carve_out"


def test_sweep_takes_the_larger_across_runs_and_rounds():
    m = _manifest()
    r1 = _source(a=[("tests/test_a.py", 1.23)], b=[("tests/test_b.py", 0.04)])
    r2 = _source(a=[("tests/test_a.py", 2.04)], b=[("tests/test_b.py", 0.0)])
    record = cm.sweep([("r1", r1), ("r2", r2)], m, cm.seed_from_manifest(m))
    # larger across runs, then 1 dp; the 0.04/0.0 pair floors at 0.1.
    assert record["rows"]["test_a.py"]["value"] == 2.0
    assert record["rows"]["test_a.py"]["samples"] == {"r1": 1.23, "r2": 2.04}
    assert record["rows"]["test_b.py"]["value"] == 0.1


def test_sweep_records_unmeasured_rows_explicitly_and_carries_forward():
    m = _manifest()
    m["durations"] = {"test_a.py": 7.5}
    source = _source(b=[("tests/test_b.py", 1.0)])
    record = cm.sweep([("r1", source)], m, cm.seed_from_manifest(m))
    # test_a was never measured in this run: its committed value is carried
    # forward and MARKED, never silently defaulted to PROVISIONAL_WEIGHT.
    assert record["rows"]["test_a.py"]["unmeasured"] is True
    assert record["rows"]["test_a.py"]["value"] == 7.5
    # a file with no prior value gets the explicit provisional weight.
    m2 = _manifest()
    record2 = cm.sweep([("r1", source)], m2, cm.seed_from_manifest(m2))
    assert record2["rows"]["test_a.py"]["unmeasured"] is True
    assert record2["rows"]["test_a.py"]["value"] == cm.PROVISIONAL_WEIGHT


def test_sweep_retains_a_declared_row_even_when_measured(monkeypatch):
    # The #4766 RETAINED rule: a row no artifact can re-derive keeps its value.
    monkeypatch.setitem(cm.RETAINED, "test_a.py", "retained for the test")
    m = _manifest()
    m["durations"] = {"test_a.py": 28.1}
    record = cm.sweep([("r1", _source(a=[("tests/test_a.py", 3.1)]))], m,
                      cm.seed_from_manifest(m))
    row = record["rows"]["test_a.py"]
    assert row["value"] == 28.1 and row["retained"] is True
    assert row["unmeasured"] is True


def test_partial_artifact_set_retains_rather_than_drops():
    # #5050 Task 2: a source run that measured a row before is now absent (a
    # leg was cut), so the reading is incomplete and must NOT move the weight
    # down — the committed value is retained and the row marked.
    m = _manifest()
    m["durations"] = {"test_a.py": 30.0}
    prior = cm.sweep(
        [("r1", _source(a=[("tests/test_a.py", 30.0)])),
         ("r2", _source(a=[("tests/test_a.py", 28.0)]))],
        m, cm.seed_from_manifest(m))
    assert prior["rows"]["test_a.py"]["samples"] == {"r1": 30.0, "r2": 28.0}
    # r2's artifact is gone; only r1 reports, and it reports a much lower value.
    record = cm.sweep([("r1", _source(a=[("tests/test_a.py", 5.0)]))],
                      m, prior)
    row = record["rows"]["test_a.py"]
    assert row["value"] == 30.0, row
    assert row["unmeasured"] is True and row["retained"] is True, row
    # ... and the retained row is not mistaken for a stale marker.
    m["durations"]["test_a.py"] = 30.0
    issues = cm.value_issues(m, record)
    assert not any("test_a.py" in i and "stale marker" in i for i in issues), issues


def test_partial_sample_universe_is_a_fixed_point():
    # The guarantee must be a fixed point, not a one-shot: if a partial sweep
    # replaced the sample set with the partial one (or dropped it entirely),
    # the next sweep would find no missing run and silently drop the value.
    # The absent run's sample must be carried forward on EVERY partial reading —
    # lowering, equal, raising, and zero-sample.
    for first_partial in (5.0, 30.0, 35.0, None):
        m = _manifest()
        m["durations"] = {"test_a.py": 30.0}
        prior = cm.sweep(
            [("r1", _source(a=[("tests/test_a.py", 30.0)])),
             ("r2", _source(a=[("tests/test_a.py", 28.0)]))],
            m, cm.seed_from_manifest(m))
        if first_partial is None:
            src = _source()
        else:
            src = _source(a=[("tests/test_a.py", first_partial)])
        once = cm.sweep([("r1", src)], m, prior)
        assert {"r1", "r2"} <= set(once["rows"]["test_a.py"]["samples"]), \
            (first_partial, once["rows"]["test_a.py"])
        # A later, much lower partial reading must not fall below the floor the
        # retained reading established.
        twice = cm.sweep([("r1", _source(a=[("tests/test_a.py", 5.0)]))],
                         m, once)
        row = twice["rows"]["test_a.py"]
        assert row["value"] == once["rows"]["test_a.py"]["value"], \
            (first_partial, once["rows"]["test_a.py"], row)
        assert row["value"] >= 30.0, (first_partial, row)


def test_a_complete_sample_set_may_lower_a_value():
    # The complement: when the sample set is at least as complete as the
    # prior's, the max is a genuine reading and may legitimately move down.
    m = _manifest()
    m["durations"] = {"test_a.py": 30.0}
    prior = cm.sweep(
        [("r1", _source(a=[("tests/test_a.py", 30.0)]))],
        m, cm.seed_from_manifest(m))
    record = cm.sweep([("r1", _source(a=[("tests/test_a.py", 5.0)]))],
                      m, prior)
    assert record["rows"]["test_a.py"]["value"] == 5.0


def test_sweep_honours_the_pinned_equality(monkeypatch):
    monkeypatch.setitem(cm.PINS, "test_b.py", "test_a.py")
    m = _manifest()
    record = cm.sweep([("r1", _source(a=[("tests/test_a.py", 9.9)],
                                      b=[("tests/test_b.py", 0.2)]))],
                      m, cm.seed_from_manifest(m))
    assert record["rows"]["test_b.py"]["value"] == record["rows"]["test_a.py"]["value"]
    assert record["rows"]["test_b.py"]["pinned_to"] == "test_a.py"


def test_sweep_drops_a_dead_carve_only_row():
    # #4783: a carve-out-only key is dead (the carve-out job does not consume
    # the map) and must not survive into the record.
    m = _manifest()
    m["durations"] = {"test_carve.py": 20.0, "test_a.py": 1.0}
    record = cm.sweep([("r1", _source(carve=[("tests/test_carve.py", 20.0)]))],
                      m, cm.seed_from_manifest(m))
    assert "test_carve.py" not in record["rows"]
    assert "test_a.py" in record["rows"]


# ── rendering / rewriting the manifest in place ───────────────────────────


def test_rewrite_is_idempotent_when_the_pinned_row_sorts_first(tmp_path):
    # The pinned row's explanation is rendered as INDENTED comments above the
    # row. If the header scan absorbed them (it treated `  #` as header), each
    # rewrite would append another copy — `render_rows` is not idempotent and
    # the "regenerable" property fails.
    path = tmp_path / "ci-surfaces.yml"
    path.write_text("surfaces:\n  core:\n    - test_a.py\n"
                    "durations:\n# prose header\n")
    record = {"rows": {
        "test_a.py": {"leg": "fast", "samples": {"r": 1.0}, "value": 1.0,
                      "pinned_to": "test_b.py",
                      "note": "pinned equal to test_b.py (test-enforced)"},
        "test_b.py": {"leg": "fast", "samples": {}, "value": 0.5,
                      "unmeasured": True, "note": "carried forward"},
    }}
    cm.rewrite_manifest(path, record)
    once = path.read_text()
    assert re.search(r"(  # .*\n)+  test_a\.py:", once), once
    assert once.count("pinned equal to test_b.py") == 1
    cm.rewrite_manifest(path, record)
    assert path.read_text() == once, "rewrite is not idempotent"


def test_rewrite_preserves_the_prose_header_and_trailing_keys(tmp_path):
    path = tmp_path / "ci-surfaces.yml"
    path.write_text(
        "surfaces:\n  core:\n    - test_a.py\n"
        "durations:\n"
        "# decision-carrying header the generator does NOT own\n"
        "# second header line\n"
        "  stale.py: 99.0\n"
        "guard_inputs:\n  core:\n    - config/pipelines.yaml\n")
    record = {"rows": {
        "test_a.py": {"leg": "fast", "samples": {"r": 1.0}, "value": 1.0},
        "test_b.py": {"leg": "fast", "samples": {}, "value": 2.0,
                      "unmeasured": True, "note": "carried forward"},
    }}
    cm.rewrite_manifest(path, record)
    text = path.read_text()
    assert "# decision-carrying header" in text
    assert text.index("durations:") < text.index("guard_inputs:")
    assert "stale.py" not in text
    assert "  test_b.py: 2  # unmeasured — carried forward" in text
    # the rendered order is by weight, descending (within the rows block).
    tail = text[text.index("durations:"):]
    assert tail.index("test_b.py") < tail.index("test_a.py")


# ── value validation ─────────────────────────────────────────────────────


def test_consistent_manifest_and_record_are_clean():
    m, record = _record_and_manifest()
    assert cm.value_issues(m, record) == []
    assert cm.value_issues(m, record) == []  # deterministic


def test_generic_dead_durations_key_fails():
    # A stale/renamed file left in `durations` by a hand-edit is the more
    # general form of the carve-out case (#4783): the packer drops it silently.
    m, record = _record_and_manifest()
    m["durations"]["test_ghost_5050.py"] = 1.0
    issues = cm.value_issues(m, record)
    assert any("test_ghost_5050.py" in i and "not a fast-pool or slow file" in i
               for i in issues), issues


def test_malformed_record_is_named_not_raised():
    # #3407 totality: a half-written/hand-corrupted record must be NAMED by
    # the gate, not raise inside it (which would also suppress every other
    # problem the check chain would have reported).
    m, _ = _record_and_manifest()
    bad_records = [
        [],                                                   # a list, not an object
        {"rows": []},                                         # rows not a mapping
        {"rows": {"test_a.py": "nope"}},                    # a row, not an object
        {"rows": {"test_a.py": {"leg": "fast", "samples": {}}}},  # no value
    ]
    for bad in bad_records:
        issues = cm.value_issues(m, bad)
        assert issues, bad


def test_nan_value_is_a_difference():
    # `abs(nan - 1) > 1e-9` is False, so an unguarded compare reads NaN as
    # EQUAL and a corrupted row silently backs its manifest value.
    m, record = _record_and_manifest()
    m["durations"]["test_a.py"] = float("nan")
    assert any("test_a.py" in i for i in cm.value_issues(m, record))
    m2, record2 = _record_and_manifest()
    record2["rows"]["test_a.py"]["value"] = float("nan")
    assert any("test_a.py" in i for i in cm.value_issues(m2, record2))


@pytest.mark.parametrize("bad", [0.1, 1880.0])
def test_value_mismatch_fails(bad):
    m, record = _record_and_manifest()
    m["durations"]["test_a.py"] = bad
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i and "measurement record" in i for i in issues), issues


def test_missing_row_fails():
    m, record = _record_and_manifest()
    del m["durations"]["test_b.py"]
    issues = cm.value_issues(m, record)
    assert any("test_b.py" in i and "no durations row" in i for i in issues), issues


def test_carve_out_key_fails():
    m, record = _record_and_manifest()
    m["durations"]["test_carve.py"] = 20.0
    issues = cm.value_issues(m, record)
    assert any("test_carve.py" in i and "carve-out" in i for i in issues), issues


def test_key_with_no_record_row_fails():
    m, record = _record_and_manifest()
    del record["rows"]["test_a.py"]
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i and "no row in the measurement record" in i
               for i in issues), issues


def test_stale_unmeasured_marker_fails():
    m, record = _record_and_manifest()
    record["rows"]["test_a.py"]["unmeasured"] = True
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i and "stale marker" in i for i in issues), issues


def test_absent_record_fails(tmp_path, monkeypatch):
    m, _ = _record_and_manifest()
    monkeypatch.setattr(cm, "RECORD", tmp_path / "missing.json")
    issues = cm.value_issues(m, None)
    assert any("durations-source.json" in i for i in issues), issues


def test_a_huge_int_does_not_crash_the_value_check():
    # #3407 totality: a value beyond float range must be NAMED, not raise
    # OverflowError inside the gate that exists to report it.
    m, record = _record_and_manifest()
    m["durations"]["test_a.py"] = 10 ** 400
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i for i in issues), issues


# ── the partition invariant (+ #4835) ────────────────────────────────────


def test_partition_is_clean_on_the_committed_manifest():
    assert cm.partition_issues(cs.load_manifest()) == []


def test_partition_flags_a_file_dropped_from_every_leg(monkeypatch):
    m = cs.load_manifest()
    real = cs.push_legs(m)
    broken = {"half_a": real["half_a"][1:], "half_b": real["half_b"],
              "slow": real["slow"], "env_broken": real["env_broken"],
              "carve_out": real["carve_out"]}
    monkeypatch.setattr(cs, "push_legs", lambda manifest: broken)
    issues = cm.partition_issues(m)
    assert any("NO push leg" in i for i in issues), issues


def test_partition_flags_a_file_in_two_legs(monkeypatch):
    m = cs.load_manifest()
    real = cs.push_legs(m)
    broken = dict(real)
    broken["half_b"] = real["half_b"] + real["half_a"][:1]
    monkeypatch.setattr(cs, "push_legs", lambda manifest: broken)
    issues = cm.partition_issues(m)
    assert any("more than one leg" in i for i in issues), issues


def test_partition_allows_an_unclassified_push_extra(monkeypatch):
    # `push_legs()` appends `push_extra` to the halves BY DESIGN and
    # `leg_coverage_issues()` requires those files to stay unclassified, so the
    # reverse check must not red the shape the repo's own guard mandates.
    m = cs.load_manifest()
    real = cs.push_legs(m)
    broken = dict(real)
    broken["half_a"] = [*list(real["half_a"]), "bench/test_extra_5050"]
    m["push_extra"] = ["bench/test_extra_5050"]
    monkeypatch.setattr(cs, "push_legs", lambda manifest: broken)
    assert cm.partition_issues(m) == []


def test_env_broken_file_is_not_reported_as_a_coverage_hole():
    # #4835: fast_files_absent_from_halves disagreed with fast_pool about the
    # env-broken set, reporting a permanent FALSE hole for test_agent_signup.py.
    m = cs.load_manifest()
    legs = cs.push_legs(m)
    halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
    absent = cs.fast_files_absent_from_halves(m, halves)
    assert "test_agent_signup.py" not in absent, absent
    assert absent == [], absent


# ── guard reachability (#3362/#4115/#4186/#4658) ─────────────────────────


def test_guard_reachability_is_clean_on_the_committed_tree():
    assert cm.guard_reachability_issues(cs.load_manifest()) == []


def test_guard_reachability_flags_a_dead_source_pattern(monkeypatch):
    m = cs.load_manifest()
    monkeypatch.setitem(
        cs.SOURCE_PATTERNS, "onboarding",
        (*cs.SOURCE_PATTERNS["onboarding"], "tools/__does_not_exist_5050__.py"))
    issues = cm.guard_reachability_issues(m)
    assert any("__does_not_exist_5050__" in i for i in issues), issues


def test_guard_reachability_flags_a_misdeclared_guard_input():
    # website/license.html selects `onboarding`; declaring it under `core` means
    # a PR editing the guarded page would not run the guard that reads it.
    m = dict(cs.load_manifest())
    m["guard_inputs"] = {"core": ["website/license.html"]}
    issues = cm.guard_reachability_issues(m)
    assert any("website/license.html" in i for i in issues), issues


def test_guard_reachability_flags_an_unselectable_tool_guard(monkeypatch):
    # The guard stays REGISTERED; the derivation that makes its tool selectable
    # is what is removed. This is the #3362/#4115 state on a tree before the
    # rule existed: `tests/test_surface_manifest.py` classified under `core`,
    # `tools/surface_manifest.py` selecting nothing.
    m = cs.load_manifest()
    monkeypatch.setattr(cs, "_tool_guard_surface", lambda path, manifest: [])
    issues = cm.guard_reachability_issues(m)
    assert any("tools/surface_manifest.py" in i for i in issues), issues


def test_a_tool_with_a_registered_guard_is_selectable():
    # The derived rule (ci_selection._tool_guard_surface) is what makes the
    # check above pass on the committed tree.
    r = cs.select(["tools/surface_manifest.py"], "pull_request",
                  cs.load_manifest())
    assert r["surfaces"] == ["core"], r
    assert "test_surface_manifest.py" in r["test_files"], r


def test_the_two_arch_docs_select_the_onboarding_guard():
    # #4658: a docs-only PR editing a guarded doc must run the guard.
    m = cs.load_manifest()
    for doc in ("docs/auth-architecture.md", "website/website_architecture.md"):
        r = cs.select([doc], "pull_request", m)
        assert "onboarding" in r["surfaces"], (doc, r)
        assert "test_no_legacy_token_path.py" in r["test_files"], (doc, r)


# ── bootstrap: atomic registration (#4348/#4364/#4817) ───────────────────


def _write_scratch(tmp_path, manifest, record):
    mpath = tmp_path / "ci-surfaces.yml"
    rpath = tmp_path / "record.json"
    body = {k: v for k, v in manifest.items() if k != "durations"}
    mpath.write_text(yaml.safe_dump(body) + "durations:\n# header\n")
    cm.write_record(record, rpath)
    return mpath, rpath


def test_register_provisional_keeps_the_contract_green(tmp_path):
    m, record = _record_and_manifest()
    m["surfaces"]["core"].append("test_new.py")
    mpath, rpath = _write_scratch(tmp_path, m, record)
    added = cm.register_provisional(["test_new.py"], mpath, rpath)
    assert added == ["test_new.py"]
    loaded_m = yaml.safe_load(mpath.read_text())
    loaded_record = cm.load_record(rpath)
    assert loaded_record["rows"]["test_new.py"]["unmeasured"] is True
    assert loaded_record["rows"]["test_new.py"]["value"] == cm.PROVISIONAL_WEIGHT
    assert loaded_m["durations"]["test_new.py"] == cm.PROVISIONAL_WEIGHT
    # the bootstrap trap is closed: a brand-new file with no measurement no
    # longer reds the strict presence check.
    assert cm.value_issues(loaded_m, loaded_record) == []


def test_register_provisional_uses_the_derived_leg(tmp_path):
    # A file registered into the slow pool must not get a hardcoded `fast` leg.
    m = _manifest()
    m["slow_files"].append("test_slownew.py")
    m["surfaces"]["core"].append("test_slownew.py")
    mpath, rpath = _write_scratch(tmp_path, m, {"rows": {}})
    cm.register_provisional(["test_slownew.py"], mpath, rpath)
    assert cm.load_record(rpath)["rows"]["test_slownew.py"]["leg"] == "slow"


def test_register_provisional_is_idempotent(tmp_path):
    m, record = _record_and_manifest()
    m["surfaces"]["core"].append("test_new.py")
    mpath, rpath = _write_scratch(tmp_path, m, record)
    cm.register_provisional(["test_new.py"], mpath, rpath)
    assert cm.register_provisional(["test_new.py"], mpath, rpath) == []


# ── end-to-end CLI: sweep then check ─────────────────────────────────────


def test_cli_sweep_then_check_round_trip(tmp_path):
    m = _manifest()
    mpath, rpath = _write_scratch(tmp_path, m, {"rows": {}})
    junit = tmp_path / "run"
    _junit(junit, "pytest-log-test-a", [("tests/test_a.py", 4.04)])
    _junit(junit, "pytest-log-test-b", [("tests/test_b.py", 1.5)])
    _junit(junit, "pytest-log-test-slow", [("tests/test_slow.py", 12.0)])
    _junit(junit, "pytest-log-test-carve-out",
           [("tests/test_carve.py", 20.0), ("tests/test_both.py", 5.0)])
    assert cm.main(["sweep", "--junit-dir", str(junit), "--manifest", str(mpath),
                    "--record", str(rpath), "--write"]) == 0
    assert cm.main(["check", "--manifest", str(mpath), "--record", str(rpath)]) == 0
    # a hand-edit is caught by the same check.
    text = mpath.read_text().replace("test_a.py: 4", "test_a.py: 1880")
    mpath.write_text(text)
    assert cm.main(["check", "--manifest", str(mpath), "--record", str(rpath)]) == 1
