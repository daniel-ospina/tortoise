"""Tests for the two-arm S3 prior-injection ablation probe (#10463).

The probe measures a pre-registered hypothesis, so the tests that matter are the ones that pin the
ARITHMETIC and the REFUSALS — not the run: a verdict mapping that could be re-read generously after
a result, a concentration statistic that divides by zero, an evidence path that escapes the bundle,
or a bars source that a worktree edit could serve.

The run itself needs a live graph and a model key, so it is not exercised here; `tests/` runs
keyless in CI, and a test that needed the key would be a test that never runs.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools/s3_prior_ablation.py"


def _probe():
    spec = importlib.util.spec_from_file_location("_s3_prior_ablation", PROBE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _probe()


BARS = {
    "primary_ratio_max": 0.75,
    "guard_ratio_min": 1.0,
    "corroboration_min_sessions": 4,
    "sessions_total": 7,
    "arm_integrity_required": 14,
    "concentration_max": 0.60,
    "cost_ratio_max": 2.0,
}
FLOORS = {"created_nodes_off": 30, "real_facts_off": 20, "sessions": 7}


def _arm(proven_per_session: int, created: list[int], facts: int, operator_edges: int,
         llm_calls: int = 21) -> dict:
    sessions = [
        {
            "session_id": f"wp{i:02d}_x",
            "created": {"extraction_nodes": value, "extraction_claims": value,
                        "objects": 0, "events": 0, "label_instances": value,
                        "points": value, "operator_edges": 0},
            "s3": {"proven": i < proven_per_session, "state": "resolved"},
            "llm_calls": llm_calls // 7,
            "ok": True,
        }
        for i, value in enumerate(created)
    ]
    return {
        "sessions": sessions,
        "real_facts": {"survived": facts, "total": 90},
        "totals": {"extraction_nodes": sum(created), "operator_edges": operator_edges,
                   "label_instances": sum(created)},
        "llm_calls": llm_calls,
    }


def test_arm_integrity_precedes_any_ratio(probe):
    """A degraded/suppressed-but-unproven arm must produce ARM-INVALID and NO ratio.

    This is the pre-registration's rule 1: a run whose ON lookup degraded measures the
    degradation (#3679), so a ratio computed from it would be a ratio about the wrong thing.
    """
    off = _arm(proven_per_session=6, created=[40] * 7, facts=60, operator_edges=10)
    on = _arm(proven_per_session=7, created=[10] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_ARM_INVALID
    assert result["ratio"] is None
    assert "13 of 14" in result["why"]


def test_ratio_near_one_is_falsified(probe):
    """The falsifier: telling extraction what exists does NOT reduce creates."""
    off = _arm(14, [40] * 7, facts=60, operator_edges=10)
    on = _arm(14, [40] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["ratio"] == pytest.approx(1.0)
    assert result["primary_met"] is False


def test_primary_bar_met_with_guards_held_is_supported(probe):
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_SUPPORTED
    assert result["ratio"] == pytest.approx(0.5)


def test_guard_violation_falsifies_even_when_the_ratio_passes(probe):
    """The epic's tension rule: volume may not be bought by extracting less real content."""
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=40, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["guard_facts"] is False
    assert "GUARD-VIOLATED" in result["verdict_text"]


def test_operator_guard_alone_also_falsifies(probe):
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=60, operator_edges=4)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_FALSIFIED
    assert result["guard_operators"] is False


def test_floors_refuse_rather_than_score(probe):
    """An under-powered OFF arm is REFUSED, never a ratio (and never a 0)."""
    off = _arm(14, [2] * 7, facts=60, operator_edges=10)
    on = _arm(14, [1] * 7, facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_REFUSED
    assert result["ratio"] is None
    assert "floor" in result["why"]

    starved = _arm(14, [100] * 7, facts=5, operator_edges=10)
    result = probe.decide(BARS, FLOORS, starved, _arm(14, [50] * 7, 60, 10))
    assert result["verdict"] == probe.VERDICT_REFUSED
    assert "planted units" in result["why"]


def test_concentration_is_undefined_when_nothing_fell(probe):
    """A share of zero reduction is undefined — reported None, never a clean-looking 0."""
    assert probe.concentration([(10, 10), (10, 12)]) == {
        "share": None, "largest_session_reduction": 0, "total_reduction": 0}


def test_concentration_flags_one_session_carrying_the_result(probe):
    """The n=7 trap: one session supplying almost all of the reduction."""
    stats = probe.concentration([(100, 10), (10, 10), (10, 10)])
    assert stats["total_reduction"] == 90
    assert stats["share"] == pytest.approx(1.0)
    off = _arm(14, [100, 10, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    on = _arm(14, [10, 10, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert "SINGLE-SESSION-DRIVEN" in (result["qualifier"] or "")


def test_an_increase_cannot_subsidise_another_sessions_share(probe):
    stats = probe.concentration([(10, 40), (100, 10)])
    assert stats["total_reduction"] == 90
    assert stats["share"] == pytest.approx(1.0)


def test_outside_the_evidence_bundle_is_refused(probe):
    assert probe.inside_evidence(probe.EVIDENCE_DIR / "x.log")
    assert not probe.inside_evidence(ROOT / "docs/evidence/other/x.log")
    assert not probe.inside_evidence(Path("/tmp/x.log"))


def test_only_non_production_paths_count_as_nothing_changed(probe):
    """`docs/`, `tools/` and `tests/` are not production code; `tortoise/` is.

    The first cut allowed only `docs/evidence/` + `tools/`, which made the probe's own documented
    reproduce command REFUSE at its own PR head (the PR also touches `docs/architecture/` and
    `tests/`) — a refusal that would have been read as "production code changed" (review r1 P1).
    The corpus under `tests/eval/write_path/` is allowed here because it is hash-guarded instead.
    """
    assert probe.is_evidence_or_tool("docs/evidence/5087-hypothesis/x.log")
    assert probe.is_evidence_or_tool("docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md")
    assert probe.is_evidence_or_tool("tools/s3_prior_ablation.py")
    assert probe.is_evidence_or_tool("tests/test_s3_prior_ablation.py")
    assert not probe.is_evidence_or_tool("tortoise/extractor_v2.py")
    assert not probe.is_evidence_or_tool("config/routing.yml")


def test_bars_are_parsed_from_the_pins_block(probe):
    text = (ROOT / probe.PREREG_REL).read_text()
    pins = probe.parse_pins(text)
    assert pins["hypothesis_bars"]["primary_ratio_max"] == 0.75
    assert pins["denominator_floors"]["created_nodes_off"] == 30
    assert len(pins["source_sha"]) == 40
    # The bars the probe compares against are these, never literals in the module.
    source = PROBE.read_text()
    assert "primary_ratio_max = 0.75" not in source


def test_a_missing_pins_block_refuses(probe):
    with pytest.raises(SystemExit):
        probe.parse_pins("no pins here")


def test_the_census_is_reused_not_reimplemented(probe):
    """#10463 forbids a second census: the probe must import capstone_measure's."""
    source = PROBE.read_text()
    assert "capstone_measure.py" in source
    assert "def census(" not in source
    assert "MATCH (n) UNWIND labels(n)" not in source


def test_witness_must_be_this_sessions_and_count_REAL_calls(probe):
    """Arm integrity cannot be inherited, arm-total, or counted from the STUB (r1 P1, r2 P1).

    An empty witness proves nothing however healthy the blob reads; ON needs at least one real call
    IN THIS SESSION; and OFF must be judged on calls to the REAL function — the OFF stub is invoked
    once per session, so a counter the stub increments makes every OFF session unprovable by
    construction (ARM-INVALID 0 of 7, always). The third argument is REAL calls, per session.
    """
    healthy = {"mode": "real", "degraded": False, "queries_run": 3}
    assert probe.witness_ok("ON", healthy, 1)
    assert not probe.witness_ok("ON", healthy, 0)
    assert not probe.witness_ok("ON", {}, 1)
    stub = {"mode": "real", "degraded": False, "queries_run": 0}
    assert probe.witness_ok("OFF", stub, 0)      # stub ran, real function did not: PROVEN
    assert not probe.witness_ok("OFF", stub, 1)  # the real function ran in the OFF arm: unproven
    assert not probe.witness_ok("OFF", {}, 0)


def test_the_session_floor_refuses(probe):
    """`denominator_floors.sessions` is a REFUSED floor, not decoration (review r1 P2)."""
    # The integrity bar is lowered so the SESSION floor is the thing under test: at the real bar
    # (14 of 14) a short arm is refused by integrity first, which is the correct precedence.
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 6, facts=60, operator_edges=10)   # short arm
    result = probe.decide(dict(BARS, arm_integrity_required=12), FLOORS, off, on)
    assert result["verdict"] == probe.VERDICT_REFUSED
    assert result["ratio"] is None


def test_unequal_arms_are_refused_rather_than_silently_truncated(probe):
    """The per-session table pairs the arms row by row, so a short arm must REFUSE.

    `zip` truncates to the shorter iterable, so a 7-session OFF arm against a 6-session ON arm
    printed a table that looked complete while the seventh session was simply absent from it. The
    refusal is exit 3 — an unmeasurable run, never 1, which is what a crash looks like.
    """
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on_short = _arm(14, [50] * 6, facts=60, operator_edges=10)
    assert len(probe.paired_sessions(off, off)) == 7
    with pytest.raises(SystemExit) as refusal:
        probe.paired_sessions(off, on_short)
    assert refusal.value.code == 3


def test_a_non_majority_result_is_qualified_not_silently_omitted(probe):
    """E2b is a registered QUALIFIER: 2 of 7 sessions falling must be said out loud."""
    off = _arm(14, [100, 100, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    on = _arm(14, [40, 40, 10, 10, 10, 10, 10], facts=60, operator_edges=10)
    result = probe.decide(BARS, FLOORS, off, on)
    assert result["corroboration_met"] is False
    assert "not-majority-of-sessions" in (result["qualifier"] or "")


def test_the_guard_floor_comes_from_the_bars(probe):
    """The guards compare against `guard_ratio_min`, never a hardcoded 1."""
    loose = dict(BARS, guard_ratio_min=0.5)
    off = _arm(14, [100] * 7, facts=60, operator_edges=10)
    on = _arm(14, [50] * 7, facts=40, operator_edges=5)
    assert probe.decide(BARS, FLOORS, off, on)["guard_facts"] is False
    assert probe.decide(loose, FLOORS, off, on)["guard_facts"] is True


def test_the_off_stub_never_increments_the_real_call_counter():
    """The exact cycle-2 regression: a stub-incremented counter makes OFF unprovable forever.

    Pinned on the SOURCE text because the counters live inside `run_arm`'s closures, which need a
    live graph to exercise. `off_stub` must not touch `real_session` — the OFF arm's proof is
    "the REAL function was never called", so a stub call is evidence of nothing.
    """
    import re

    source = PROBE.read_text()
    body = re.search(r"def off_stub\(.*?\n(?=    ev\.search_graph)", source, re.S)
    assert body, "off_stub not found — the OFF arm's behaviour moved"
    assert "real_session" not in body.group(0)
    assert 'calls["intercepted"] += 1' in body.group(0)


# ── the metric-definition pin (review r3 P2a) ──────────────────────────────
#
# The path allowlist answers "is this production code?". It does not answer "is this the code that
# COMPUTED the number?", and the three artifacts that do are all under an allowed prefix: the census
# (`created_new_nodes`, `operator_edges`), `grading.macro_survival_counts` (`real_facts`) and
# `corpus.compute_fixtures_hash` (the corpus guard itself). A prefix allowlist alone lets those
# differ between the pinned `source_sha` and `HEAD` without a refusal — a re-defined metric beside an
# unchanged log, which reads as an unchanged measurement.


def test_the_code_that_computes_the_numbers_is_pinned_too(probe):
    """The allowlist admits them; the metric pin must still refuse them."""
    for rel in (
        "docs/evidence/7949-capstone/probes/capstone_measure.py",
        "tests/eval/write_path/grading.py",
        "tests/eval/write_path/corpus.py",
    ):
        assert probe.is_evidence_or_tool(rel), f"{rel}: the allowlist alone would admit this"
        assert probe.is_metric_artifact(rel), f"{rel}: the metric pin must refuse this"
    # …and it must not refuse everything: the probe, the log and the docs are not metrics.
    for rel in (
        "tools/s3_prior_ablation.py",
        "docs/evidence/5087-hypothesis/README.md",
        "docs/evidence/5087-hypothesis/s3-ablation-a1075dfc.log",
        "tests/test_s3_prior_ablation.py",
        "tortoise/extractor_v2.py",
    ):
        assert not probe.is_metric_artifact(rel), rel


def _git(repo, *args):
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          check=False)
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout.strip()


def test_a_changed_metric_artifact_refuses_the_run(probe, monkeypatch, tmp_path):
    """Behavioural, not a source poke: a moved metric definition must exit 3 (REFUSED).

    Two temp repos, same shape, one variable: the second commit touches a metric artifact in the
    first and an ordinary non-production file in the second. The first must refuse — that is the
    guarantee the README states — and the second must be admitted, so the guard is not refusing
    everything that is merely under an allowed prefix.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    def build(name: str, touched: str) -> tuple[Path, str, str]:
        repo = tmp_path / name
        for rel in ("tools", "tests/eval/write_path", "docs/evidence/7949-capstone/probes"):
            (repo / rel).mkdir(parents=True, exist_ok=True)
        (repo / "tools/s3_prior_ablation.py").write_text("# probe\n")
        (repo / "tests/eval/write_path/grading.py").write_text("REAL = 1\n")
        (repo / "tests/eval/write_path/corpus.py").write_text("HASH = 1\n")
        (repo / "docs/evidence/7949-capstone/probes/capstone_measure.py").write_text("C = 1\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "baseline")
        source_sha = _git(repo, "rev-parse", "HEAD")
        target = repo / touched
        target.parent.mkdir(parents=True, exist_ok=True)
        prior = target.read_text() if target.exists() else ""
        target.write_text(prior + "\n# moved after the pin\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "move one file")
        return repo, source_sha, _git(repo, "rev-parse", "HEAD")

    metric_repo, source_sha, head = build("metric", "tests/eval/write_path/grading.py")
    monkeypatch.setattr(probe, "ROOT", metric_repo)
    with pytest.raises(SystemExit) as refusal:
        probe.assert_source_sha(source_sha, head)
    assert refusal.value.code == 3, "a moved metric artifact is an unmeasurable run, never a crash"

    other_repo, source_sha, head = build("other", "tools/another_probe.py")
    monkeypatch.setattr(probe, "ROOT", other_repo)
    assert probe.assert_source_sha(source_sha, head) == ["tools/another_probe.py"]


# ── the log has to survive the run (review r4 P2-3) ────────────────────────
#
# The measured run's OUTPUT — not only its definition — has to be storable without the process's
# cooperation. The pre-fix probe buffered every line and wrote the file once, at the end, so an
# abort mid-run left no file at all. That is exactly what happened to replay A: its per-session
# noise distribution exists nowhere, which is why the bundle's ±31 % band rests on two quoted pairs.


def test_an_aborted_run_leaves_the_sessions_it_already_measured(probe, tmp_path):
    """Behavioural: lines are on DISK before the run finishes, without closing the handle.

    The `try/except` below stands in for the abort (replay A died on `arm["arm"]` indexing a string
    mid-run). Nothing closes the handle, so a file that only gets its body at exit reads empty here.
    """
    path = tmp_path / "nested" / "s3-ablation.log"
    handle, write_line = probe.open_log(path)
    try:
        write_line("--- ARM OFF — fresh graph s3ablate_test_off ---")
        write_line("  [OFF] created extraction nodes: 45")
        raise RuntimeError("the run aborted mid-arm")
    except RuntimeError:
        pass
    assert path.read_text().splitlines() == [
        "--- ARM OFF — fresh graph s3ablate_test_off ---",
        "  [OFF] created extraction nodes: 45",
    ], "an abort must leave the completed sessions behind, not an empty or missing log"
    handle.close()


def test_the_log_is_not_written_once_at_the_end():
    """The wiring, pinned on the source: `main`'s log() must go through the incremental writer.

    The writer lives in `main`'s closure and needs a live graph and a model key to exercise, so the
    end-of-run-only write is pinned here — that single one-shot write IS the defect (r4 P2-3): it is
    the reason an aborted run leaves nothing at all.
    """
    import re

    source = PROBE.read_text()
    assert not re.search(r"out_path\.write_text\(", source), (
        "a one-shot write at the end loses every measured session")
    assert "open_log(out_path)" in source, "the log must be opened before the arms run"


# ── the documented reproduce path must be executable where it is documented (r4 P2-2) ──
#
# `is_evidence_or_tool` refuses anything outside `docs/`, `tools/`, `tests/`, so the `## Reproduce`
# command is admitted only at a head whose diff from the pinned `source_sha` touches nothing else.
# `config/` moves on nearly every merge — this PR touches two of those files itself — so at the head
# the bundle lands on, the FIRST command a reader types refused at exit 3 and read as a broken
# checkout. The docs and the pin have to agree about that, and the agreement has to be pinned.

#: The head the measured run was taken at: the commit `--prereg` names, which is also the `head=`
#: the committed log prints. `718ee3b` (the pins' `source_sha`) is its parent-side pin.
MEASURED_HEAD = "a1075dfc2"


def test_the_reproduce_section_states_the_head_its_pin_admits(probe):
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "## Reproduce" in readme, "the bundle must document how to reproduce it"
    section = readme.split("## Reproduce", 1)[1]
    assert MEASURED_HEAD in section, (
        "the reproduce command is admitted only at the head its `source_sha` pins — the section "
        "must name it (r4 P2-2)")
    assert "config/" in section and "exit 3" in section, (
        "the section must say that a later head where non-evidence files (e.g. `config/`) moved is "
        "REFUSED at exit 3 BY DESIGN — otherwise the refusal reads as a broken checkout")


def test_a_later_head_that_moved_a_config_file_refuses_the_pin(probe, monkeypatch, tmp_path):
    """The rule the docs now state is the rule the code enforces (temp repos, one variable).

    `config/` is not production extraction code, but it is outside the allowlist, so the pin refuses
    it: the run must be reproduced at the pinned head. Pinning the behaviour keeps the docs honest.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    def build(name: str, touched: str) -> tuple[Path, str, str]:
        repo = tmp_path / name
        (repo / "tools").mkdir(parents=True, exist_ok=True)
        (repo / "config").mkdir(parents=True, exist_ok=True)
        (repo / "tools/s3_prior_ablation.py").write_text("# probe\n")
        (repo / "config/ci-surfaces.yml").write_text("core: []\n")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "baseline")
        source_sha = _git(repo, "rev-parse", "HEAD")
        target = repo / touched
        target.parent.mkdir(parents=True, exist_ok=True)
        prior = target.read_text() if target.exists() else ""
        target.write_text(prior + "# moved after the pin\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "move one file")
        return repo, source_sha, _git(repo, "rev-parse", "HEAD")

    config_repo, source_sha, head = build("config", "config/ci-surfaces.yml")
    monkeypatch.setattr(probe, "ROOT", config_repo)
    with pytest.raises(SystemExit) as refusal:
        probe.assert_source_sha(source_sha, head)
    assert refusal.value.code == 3

    docs_repo, source_sha, head = build("docs", "tools/another_probe.py")
    monkeypatch.setattr(probe, "ROOT", docs_repo)
    assert probe.assert_source_sha(source_sha, head) == ["tools/another_probe.py"]


# ── the write-back must not leave the doc contradicting its own measurement (r4 P2-1) ──


def test_the_architecture_doc_no_longer_calls_the_question_unmeasured(probe):
    """§10 reported the falsification, but §4.1's note still pointed at §14 as "still unmeasured".

    `#10463`'s contract says a falsified hypothesis is never re-run until it agrees, so the design
    doc is what has to settle it: :548 routed the next reader to :1758's "UNMEASURED", i.e. to the
    state that produced this lane. Both must carry the figure now, and §14 must keep the half that
    really is still open (S3's disambiguation accuracy).
    """
    doc = (ROOT / "docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md").read_text()
    assert "**Still unmeasured (§14):** whether giving extraction the neighbourhood" not in doc, (
        "§4.1's note still sends the reader to §14 for an open question this PR measured")
    assert "Whether the neighbourhood lookup changes extraction's output is UNMEASURED" not in doc, (
        "§14 still declares UNMEASURED what §10 reports as measured")
    assert "284" in doc and "247" in doc, "the measured figures must live where the claim was"
    assert "whether S3's disambiguation is accurate" in doc, (
        "the half that really IS still unmeasured must survive the edit")


# ── the pin must see the WORKING TREE, and the log must name the bytes that ran (r5 P1) ──
#
# `assert_source_sha` compares two COMMITS, so an uncommitted edit to production code, to a metric
# artifact or to the probe itself was admitted silently while the log still asserted that the pinned
# sha was the tree that ran. The measured run is an instance of that hole: its `head=` line names
# `a1075dfc2`, while the `S3 WITNESS` line it prints does not exist in the probe at that commit
# (it was an uncommitted working copy whose content equals `325f2a1a5`). So the tree is now asserted
# clean over exactly the paths that can move a number, and the log carries the probe's own sha256 —
# which names the bytes that ran whether or not they were ever committed.


def test_an_uncommitted_edit_refuses_the_run(probe, monkeypatch, tmp_path):
    """Behavioural, temp repo, one variable: clean is admitted, a dirty guarded path is exit 3.

    A dirty tree is refused because a commit id cannot certify it; a dirty path OUTSIDE the guarded
    set (the evidence bundle, `config/`) is not refused, so the guard is not simply refusing
    everything that is uncommitted.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "probe-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "probe-test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "probe-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "probe-test@example.invalid")

    repo = tmp_path / "dirty"
    for rel in ("tortoise", "tests/eval/write_path", "docs/evidence/7949-capstone/probes", "tools",
                "docs/evidence/5087-hypothesis", "config"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "tools/s3_prior_ablation.py").write_text("# probe\n")
    (repo / "tortoise/extractor_v2.py").write_text("SEARCH = 1\n")
    (repo / "tests/eval/write_path/grading.py").write_text("REAL = 1\n")
    (repo / "docs/evidence/7949-capstone/probes/capstone_measure.py").write_text("C = 1\n")
    (repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md").write_text("# prereg\n")
    (repo / "config/ci-surfaces.yml").write_text("core: []\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    monkeypatch.setattr(probe, "ROOT", repo)

    assert probe.dirty_paths() == [], "a committed tree must satisfy the guard"
    assert probe.assert_clean_tree(), "a clean tree returns the `tree=` value the log prints"

    # An uncommitted edit to the production extraction code — the case the commit diff cannot see.
    (repo / "tortoise/extractor_v2.py").write_text("SEARCH = 2  # tuned after the pin\n")
    with pytest.raises(SystemExit) as refusal:
        probe.assert_clean_tree()
    assert refusal.value.code == 3, "a dirty tree is an unmeasurable run, never a crash"

    # The same edit to a metric artifact, and to the probe itself, are refused too.
    for rel in ("tests/eval/write_path/grading.py", "tools/s3_prior_ablation.py"):
        _git(repo, "checkout", "--", "tortoise/extractor_v2.py")
        target = repo / rel
        target.write_text(target.read_text() + "# tuned after the pin\n")
        with pytest.raises(SystemExit) as refusal:
            probe.assert_clean_tree()
        assert refusal.value.code == 3, f"{rel}: a dirty metric artifact must refuse"
        _git(repo, "checkout", "--", rel)

    # …but an uncommitted change the run cannot measure through is NOT refused, so the guard stays
    # a dirty-CODE guard rather than a blanket "commit before you run".
    (repo / "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md").write_text("# prereg edited\n")
    (repo / "config/ci-surfaces.yml").write_text("core: ['x']\n")
    assert probe.dirty_paths() == [], (
        "an uncommitted docs/config change cannot move a number and must not refuse the run")
    status = _git(repo, "status", "--porcelain")
    assert "config/ci-surfaces.yml" in status and "PRE-REGISTRATION.md" in status, (
        "the repo really IS dirty there — so the empty guard above is the paths, not a clean tree")
    assert probe.assert_clean_tree()


def test_the_log_names_the_probe_bytes_not_only_a_commit(probe):
    """`head=` names a commit and a commit cannot name a working copy; `probe_sha256` can."""
    import hashlib

    assert probe.probe_sha256() == "sha256:" + hashlib.sha256(PROBE.read_bytes()).hexdigest()
    main_src = PROBE.read_text().split("def main(", 1)[1]
    assert "probe_sha256()" in main_src, "main must compute the probe's own hash"
    assert 'log(f"probe_sha256=' in main_src, (
        "the log must carry the hash of the bytes that ran — the producing revision is otherwise "
        "only reconstructible from a README note (r5 P1)")
    assert 'log(f"tree={tree}' in main_src, (
        "the log must print the working-tree state beside the changed-file line, so it can never "
        "read as a clean-tree assertion")


# ── `--overwrite` may replace an aborted run and nothing else (r5 P2) ──
#
# The flag existed for replay A (an aborted, unmeasured log). Nothing inspected the existing file,
# so the same flag truncated a log carrying `RATIO:`/`VERDICT:` — the "re-run until it is
# friendlier" path the one-log refusal exists to close, one flag away. An aborted run cannot print
# either line, so the FILE decides.


def test_overwrite_cannot_replace_a_measured_log(probe, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    path = tmp_path / "docs/evidence/5087-hypothesis/s3-ablation-test.log"
    path.parent.mkdir(parents=True)

    assert probe.out_path_refusal(path, overwrite=False) is None, "an absent log is never refused"

    path.write_text("--- ARM OFF — fresh graph x ---\n  [OFF] wp01_quarry_debug: created=45\n")
    assert probe.out_path_refusal(path, overwrite=False) is not None, (
        "an existing log is refused without --overwrite")
    assert probe.out_path_refusal(path, overwrite=True) is None, (
        "an aborted, unmeasured run must stay replaceable — that is what the flag is for")

    path.write_text("--- ARM OFF ---\nRATIO: 0.8697\nVERDICT: HYPOTHESIS-FALSIFIED\n")
    refusal = probe.out_path_refusal(path, overwrite=True)
    assert refusal is not None and "MEASURED" in refusal, (
        "the flag must not truncate a log that carries a verdict — that is the tuning path")

    assert "out_path_refusal(" in PROBE.read_text().split("def main(", 1)[1], (
        "the rule has to be wired into main, not merely available beside it")


def test_the_bundle_states_the_overwrite_rule_the_probe_enforces(probe):
    """README:168 claimed the probe "refuses to overwrite the log at all" — it refuses a MEASURED one."""
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "refuses to overwrite the log at all" not in readme, (
        "the probe refuses a log that carries a verdict; --overwrite still replaces an aborted run")
    assert "--overwrite" in readme and "RATIO" in readme, (
        "the README must state the rule the code enforces, and name what marks a log as measured")


def test_the_bundle_says_the_measured_run_came_from_an_uncommitted_probe(probe):
    """r5 P1: the log's `head=` is the commit; the producing probe was an uncommitted working copy.

    The bundle said the run was produced by `tools/s3_prior_ablation.py` "as committed in
    `325f2a1a5`", which would mean a clean tree at a commit that does not contain the `S3 WITNESS`
    line the log prints. It has to state the weaker, true thing — and with the new guards, why that
    cannot happen again.
    """
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "as committed in `325f2a1a5`" not in readme, (
        "the measured run's log prints a line its `head=` commit does not contain: the producing "
        "working copy was uncommitted")
    assert "uncommitted" in readme, "the bundle must say plainly that the probe was uncommitted"


# ── `--prereg` mistakes are exit 3, not 2 (r5 P3) ─────────────────────────
#
# The module header and README item 4 said 3; `refuse()`'s own docstring said `--prereg` mistakes
# "are the exception: they are invocation errors and exit 2". They are not: every `--prereg` failure
# goes through `refuse()`, and the only exit-2s in the module are the `--out` checks. A docstring
# that names the wrong branch is how a caller ends up treating an unmeasurable run as a retryable
# usage error, so both the behaviour and the sentence are pinned here.


def test_every_prereg_failure_is_exit_3_never_2(probe):
    bad_texts = {
        "no pins fence at all": "no pins here",
        "unterminated fence": "```yaml prereregistration-pins\nhypothesis_bars: {}\n",
        "not a mapping": "```yaml prereregistration-pins\n- a list\n```",
        "missing pinned keys": "```yaml prereregistration-pins\nhypothesis_bars: {}\n```",
    }
    for label, text in bad_texts.items():
        with pytest.raises(SystemExit) as refusal:
            probe.parse_pins(text)
        assert refusal.value.code == 3, f"{label} is an unmeasurable run, not an invocation error"

    with pytest.raises(SystemExit) as refusal:
        probe.show("0" * 40, probe.PREREG_REL)  # no such commit
    assert refusal.value.code == 3, "an unreadable --prereg commit is a refusal, not exit 2"

    doc = probe.refuse.__doc__ or ""
    assert "--prereg" not in doc or "not 2" in doc, (
        "refuse()'s docstring must not call --prereg failures exit 2 — they are exit 3")


# ── a refused run must leave NO file behind, and a missing record must refuse (r6 F3/F4) ──
#
# Two fail-opens, both in `main`'s ordering rather than in what it computes:
#
#   F3 — the log was opened and its eleven header lines written BEFORE the `DEEPSEEK_API_KEY` and
#        `TORTOISE_DB_URI` refusals, so a keyless invocation exited 3 having left a header-only
#        `*.log` INSIDE the evidence bundle. The next attempt with the same `--out` was then refused
#        at exit 2 by the one-measured-log rule, and the documented reproduce command passes no
#        `--overwrite` — one prerequisite mistake and the documented path stops working.
#   F4 — the document↔record cross-check was `if record_path.is_file()`, so deleting
#        `preregistration.json` switched the ONLY check that the bars belong to the document off,
#        with no refusal and no line in the log recording that the check had not run.
#
# Both are driven through the real `main`, with everything below the prerequisite refusals stubbed
# (git, the corpus re-derivation, the census) — the questions here are *did a file get created* and
# *did the absent record refuse*, neither of which needs a graph or a model key.


def _copy_bundle(tmp_path: Path, *, with_record: bool = True,
                 record: dict | str | None = None) -> tuple[Path, str]:
    """A minimal evidence bundle: the real pre-registration, and a record that pins it."""
    import hashlib
    import json

    source = ROOT / "docs/evidence/5087-hypothesis"
    bundle = tmp_path / "docs/evidence/5087-hypothesis"
    bundle.mkdir(parents=True)
    doc = (source / "PRE-REGISTRATION.md").read_text()
    (bundle / "PRE-REGISTRATION.md").write_text(doc)
    if with_record:
        payload = record
        if payload is None:
            payload = json.loads((source / "preregistration.json").read_text())
            payload["document_sha256"] = "sha256:" + hashlib.sha256(doc.encode()).hexdigest()
        (bundle / "preregistration.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload))
    return bundle, doc


def _stub_below_the_refusals(probe, monkeypatch, bundle: Path, doc: str) -> None:
    """Everything `main` reaches AFTER the prerequisite refusals: git, corpus, the census."""
    monkeypatch.setattr(probe, "EVIDENCE_DIR", bundle)
    monkeypatch.setattr(probe, "head_sha", lambda: "0" * 40)
    monkeypatch.setattr(probe, "show", lambda sha, rel: doc)
    monkeypatch.setattr(probe, "assert_source_sha", lambda source_sha, head: [])
    monkeypatch.setattr(probe, "assert_clean_tree", lambda: "a1075dfc2")
    monkeypatch.setattr(probe, "probe_sha256", lambda: "sha256:" + "0" * 64)
    monkeypatch.setattr(probe, "corpus_state", lambda pins: (["wp01_x"] * 7, "sha256:" + "1" * 64))


def test_a_refused_run_leaves_no_log_file_behind(probe, monkeypatch, tmp_path, capsys):
    """F3: the keyless refusal must happen BEFORE the log is created, not after its header."""
    bundle, doc = _copy_bundle(tmp_path)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    out = bundle / "s3-ablation-refused.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])

    assert refusal.value.code == 3, "a missing key is an unmeasurable run, never a crash"
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
    assert not out.exists(), (
        "a refused run must leave no file: a header-only log inside the bundle reads as the setup "
        "of a run that never started, and the next attempt is refused at exit 2 by the one-log rule "
        "while the documented reproduce command passes no --overwrite (r6 F3)")

    # …and the same for the second prerequisite, so the guard is not one-if-order lucky.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])
    assert refusal.value.code == 3
    assert "TORTOISE_DB_URI" in capsys.readouterr().err
    assert not out.exists()


def test_a_missing_machine_record_refuses_instead_of_skipping_the_cross_check(
        probe, monkeypatch, tmp_path, capsys):
    """F4: absence must refuse. `if record_path.is_file()` made the guard deletable."""
    bundle, doc = _copy_bundle(tmp_path, with_record=False)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    out = bundle / "s3-ablation-x.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])

    assert refusal.value.code == 3
    assert "preregistration.json" in capsys.readouterr().err, (
        "the refusal must name the file whose absence switched the cross-check off")
    assert not out.exists(), "a refused run must not start writing the log it refused"


@pytest.mark.parametrize(
    "record, expected",
    [
        ({"schema": "capstone-preregistration/v1"}, "document_sha256"),
        ({"document_sha256": None}, "document_sha256"),
        ("{not json", "not JSON"),
        ("[1, 2]", "JSON object"),
    ],
    ids=["no-sha-field", "null-sha-field", "unparseable", "not-an-object"],
)
def test_a_record_that_cannot_pin_the_document_refuses(
        probe, monkeypatch, tmp_path, capsys, record, expected):
    """`if want and want != got` failed open one field in: a record with no digest checked nothing."""
    bundle, doc = _copy_bundle(tmp_path, record=record)
    _stub_below_the_refusals(probe, monkeypatch, bundle, doc)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    out = bundle / "s3-ablation-x.log"

    with pytest.raises(SystemExit) as refusal:
        probe.main(["--prereg", "a1075dfc2", "--out", str(out)])

    assert refusal.value.code == 3
    assert expected in capsys.readouterr().err
    assert not out.exists()


def test_the_bundle_states_the_two_orderings_the_probe_now_enforces(probe):
    """F3/F4 are behaviour, but the bundle's disclosure list is what a reader trusts."""
    readme = (probe.EVIDENCE_DIR / "README.md").read_text()
    assert "before anything is written" in readme, (
        "the hardening list must say the prerequisite refusals precede the log creation (F3)")
    assert "a bundle whose record is missing" in readme, (
        "the reproduce section must say a missing record is REFUSED, not skipped (F4)")
