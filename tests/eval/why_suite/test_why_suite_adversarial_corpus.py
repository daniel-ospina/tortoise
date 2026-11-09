"""#2275: the ADVERSARIAL why-suite corpus — hermetic contract tests.

The real-run gate over this corpus lives in ``test_why_suite_benchmark.py``
(it needs a seeded graph); everything HERMETIC lives here: the second
fixture set is jointly pinned, the v1 corpus is untouched, the corpus-specific
vocabularies cannot bleed into each other, and the fixtures actually
DISCRIMINATE (a naive assembly scores below the bars the real one meets).

Assertions cover the properties the issue owns:

* **second jointly-pinned fixture set** — the adversarial manifest/gold render
  byte-identically (no drift), validate against the manifest (gold ↔ plant
  role resolution), and carry their OWN ``fixtures_hash``;
* **v1 is never mutated** — the v1 corpus files are byte-identical to the
  blessed A11 pilot corpus (its ``fixtures_hash`` is pinned as a literal
  here), and the two corpora's file sets are disjoint;
* **separate lane** — the adversarial baseline lives in its own file with its
  own config corpus/seed, and a v1 run compared against it is INCONCLUSIVE
  (hash + config mismatch), never a rubber stamp;
* **the fixtures assume the ASSEMBLY's real contracts** — the pinned
  threshold/cap/severity constants equal the ones the assembly applies, and
  the boundary fixture sits at variance EXACTLY == the threshold;
* **at least one bar that is not structurally 1.0** — the measured cap-bound
  NAND recovery on the adversarial plant is < 1.0 by construction, and the
  severity-priority / contested arms are reachable-1.0-only (a naive
  degradation takes them to 0.0 / below the floor).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
from eval.why_suite import adversarial, corpus, generate_corpus, judge, runner, schema  # noqa: E402

# The v1 A11 pilot corpus's fixtures_hash — PINNED AS A LITERAL.  The
# adversarial corpus is a SECOND fixture set; if it (or any later corpus) ever
# moves the v1 hash, this test names the corpus that broke the pilot lane.
V1_FIXTURES_HASH = (
    "sha256:2edfabb8e5d65e126f7c8ea7112598f956046518578f25901982ab5168db39b5"
)


# ── The second jointly-pinned fixture set ─────────────────────────────────


def test_adversarial_corpus_renders_byte_identically():
    """The committed adversarial manifest/gold are a fresh deterministic
    render (no silent corpus edit) — the same drift gate v1 has."""
    assert generate_corpus.check_drift(corpus_name=corpus.ADVERSARIAL_CORPUS) == []


def test_adversarial_corpus_validates_against_its_manifest():
    """Full manifest + gold validation, including the #2275 additive gold
    fields resolving against the manifest's planted roles."""
    manifest = corpus.load_manifest(corpus=corpus.ADVERSARIAL_CORPUS)
    gold = corpus.gold_doc(corpus=corpus.ADVERSARIAL_CORPUS)
    assert schema.validate_manifest(manifest) == []
    assert schema.validate_gold(gold, manifest) == []
    assert manifest["corpus"] == corpus.ADVERSARIAL_CORPUS
    assert manifest["protocol"] == adversarial.CORPUS_PROTOCOL
    # Every planted topic is a graded datum (exact coverage).
    planted = {t for lst in manifest["topics"].values() for t in lst}
    assert {e["point_id"] for e in gold["entries"]} == planted
    assert len(gold["entries"]) == corpus.PROFILES[corpus.ADVERSARIAL_CORPUS].points
    # A gold key inside the manifest is answer-key contamination.
    assert "gold" not in manifest


def test_adversarial_gold_carries_the_new_arms_and_they_resolve():
    """``nand_roles`` (plant order + severity) and ``favored_option_role`` are
    present exactly where the plant has them, and resolve to planted ids."""
    gold = corpus.gold_doc(corpus=corpus.ADVERSARIAL_CORPUS)
    roles = adversarial.PLANT_ROLES
    fake_role_map = {topic: {r: f"id-{r}" for r in roles[family]}
                     for family, topics in adversarial.topic_keys().items()
                     for topic in topics}
    seen_nand_roles = seen_favored = 0
    for entry in gold["entries"]:
        expected = adversarial.resolve_expected(entry, fake_role_map[entry["point_id"]])
        planted_roles = roles[entry["family"]]
        for spec in entry["expected"].get("nand_roles") or []:
            seen_nand_roles += 1
            assert spec["role"] in planted_roles
            assert spec["severity"] in {"high", "medium"}
        ids = [n["point_id"] for n in expected["expected_nands"]]
        assert ids == [f"id-{s['role']}" for s in entry["expected"].get("nand_roles") or []]
        if entry["expected"].get("favored_option_role"):
            seen_favored += 1
            assert expected["favored_option_id"] == (
                f"id-{entry['expected']['favored_option_role']}"
            )
    assert seen_nand_roles and seen_favored  # both arms are actually exercised


def test_corpora_are_disjoint_and_v1_is_untouched():
    """The v1 corpus's file set, hash and bytes are unchanged; the two corpora
    share no fixture file (a second corpus must never ride v1's pin)."""
    v1_files = {p.name for p in corpus.corpus_file_paths(corpus=corpus.DEFAULT_CORPUS)}
    adv_files = {p.name for p in corpus.corpus_file_paths(corpus=corpus.ADVERSARIAL_CORPUS)}
    assert v1_files.isdisjoint(adv_files)
    assert corpus.compute_fixtures_hash(corpus=corpus.DEFAULT_CORPUS) == V1_FIXTURES_HASH
    assert corpus.compute_fixtures_hash(
        corpus=corpus.ADVERSARIAL_CORPUS
    ) != V1_FIXTURES_HASH
    # The v1 self-manifest does not see the adversarial files as extras.
    assert corpus.verify_manifest(corpus=corpus.DEFAULT_CORPUS)["ok"] is True
    assert corpus.verify_manifest(corpus=corpus.ADVERSARIAL_CORPUS)["ok"] is True
    # The prompt artifact is pinned by CONTENT (identical across the v2→v3
    # protocol bump: the four why-questions did not change).
    assert "why" in judge.judge_prompt_text().lower()


def test_generator_validates_both_corpora_together():
    """One generator owns both corpora; its own gate must be clean."""
    assert generate_corpus.validate_committed() == []
    assert generate_corpus.check_drift() == []


# ── Separate lane (v1 A11 numbers untouched) ──────────────────────────────


def test_adversarial_lane_is_separate_and_cross_lane_compare_is_inconclusive():
    v1 = corpus.load_baseline(posture="m2", corpus=corpus.DEFAULT_CORPUS)
    adv = corpus.load_baseline(posture="m2", corpus=corpus.ADVERSARIAL_CORPUS)
    assert corpus.baseline_path(posture="m2", corpus=corpus.DEFAULT_CORPUS) != (
        corpus.baseline_path(posture="m2", corpus=corpus.ADVERSARIAL_CORPUS)
    )
    assert (v1["config"]["corpus"], adv["config"]["corpus"]) == (
        corpus.DEFAULT_CORPUS,
        corpus.ADVERSARIAL_CORPUS,
    )
    assert v1["config"]["seed"] == 42
    assert adv["config"]["seed"] == adversarial.CORPUS_SEED
    assert v1["fixtures_hash"] != adv["fixtures_hash"]
    # Each baseline validates against ITS OWN metric vocabulary.
    assert schema.validate_baseline(v1) == []
    assert schema.validate_baseline(adv) == []
    # A v1 run's metrics can never satisfy an adversarial baseline: the
    # verdict is inconclusive on the hash alone (config too).
    v1_metrics = {m: 1.0 for m in schema.metric_values(corpus.DEFAULT_CORPUS)}
    v1_metrics["false_positive_rate"] = 0.0
    assert (
        schema.compare_run(
            v1_metrics,
            adv,
            resolved_config=v1["config"],
            run_fixtures_hash=v1["fixtures_hash"],
            run_judge_pin=judge.judge_pin(),
        )
        == schema.VERDICT_INCONCLUSIVE
    )


def test_metric_vocabularies_are_corpus_scoped():
    v1 = schema.metric_values(corpus.DEFAULT_CORPUS)
    adv = schema.metric_values(corpus.ADVERSARIAL_CORPUS)
    assert v1 < adv  # adversarial is a strict superset
    assert {"nand_recovery_rate", "high_severity_nand_recovery",
            "contested_classification_accuracy"} == adv - v1
    for metric in adv:
        assert schema.METRIC_DIRECTIONS[metric] in {"maximize", "minimize"}
    # An adversarial-only metric is NOT a legal v1 published value.
    issues: list[str] = []
    schema._validate_metric_values(
        {"nand_recovery_rate": 0.8}, "x", issues, corpus=corpus.DEFAULT_CORPUS
    )
    assert issues and "unknown metric" in issues[0]


# ── The fixtures assume the ASSEMBLY's real contracts ─────────────────────


def test_pinned_constants_match_the_assembly():
    """The fixtures are built on the assembly's documented cap / threshold /
    severity rule.  If the assembly moves, the corpus must be re-derived —
    never silently test a contract nobody applies."""
    from tortoise import why
    from tortoise.search_engine import CONTESTED_VARIANCE_THRESHOLD

    assert schema.CONTESTED_VARIANCE_THRESHOLD == CONTESTED_VARIANCE_THRESHOLD
    assert schema.W4_MAX_CONFLICTS == why.W4_MAX_CONFLICTS
    assert schema.W4_MAX_DIG_DEEPER == why.W4_MAX_DIG_DEEPER
    # severity rule: mean >= 0.6 → "high"
    assert why._severity(schema.HIGH_SEVERITY_EP_MEAN) == "high"
    assert why._severity(schema.HIGH_SEVERITY_EP_MEAN - 0.01) == "medium"


def test_boundary_fixture_sits_exactly_on_the_threshold():
    variance = adversarial._boundary_variance()
    assert variance == schema.CONTESTED_VARIANCE_THRESHOLD
    # The contract is strictly greater-than: the boundary point is NOT
    # contested, while an epsilon over it is.
    assert not (variance > schema.CONTESTED_VARIANCE_THRESHOLD)
    assert variance >= schema.CONTESTED_VARIANCE_THRESHOLD  # a `>=` would flip it
    adversarial._assert_boundary_variance()  # plant-time guard is armed


def test_near_tie_and_wide_decision_favorites_are_the_planted_role():
    """The near-tie supports and the wide decision's EP-favorite must be the
    role the GOLD names (the whole point of both arms)."""
    supports = adversarial.NEAR_TIE_SUPPORT_POSTERIORS
    means = {role: a / (a + b) for role, (a, b) in supports.items()}
    assert len(set(means.values())) == 3  # strictly ordered (no tie)
    assert max(means, key=means.get) == "support_c"
    gold = corpus.gold_doc(corpus=corpus.ADVERSARIAL_CORPUS)
    near_tie_gold = [e for e in gold["entries"] if e["family"] == "near_tie"]
    assert all(
        t["target_role"] == "support_c"
        for e in near_tie_gold
        for t in e["expected"]["dig_deeper_targets"]
        if t["kind"] == "supports"
    )
    wide = [e for e in gold["entries"] if e["family"] == "wide_decision"]
    assert all(
        e["expected"]["favored_option_role"] == "option_c"
        and [t["target_role"] for t in e["expected"]["dig_deeper_targets"] if t["kind"] == "tradeoff"]
        == ["option_c"]
        for e in wide
    )


# ── The bars: one measured < 1.0, the others non-trivial ──────────────────


def test_nand_recovery_bar_cannot_be_structurally_one():
    """``multi_nand`` plants 3 NANDers per point against a ≤2 surfacing cap,
    so the cap-bound recovery on this corpus is < 1.0 BY CONSTRUCTION — the
    acceptance-criterion bar that is not structurally guaranteed 1.0."""
    gold = corpus.gold_doc(corpus=corpus.ADVERSARIAL_CORPUS)
    planted = surfaced = 0
    for entry in gold["entries"]:
        expected = entry["expected"]
        if expected.get("resolved") or not expected.get("nand_roles"):
            continue  # resolved rows are graded by navigation (the #2490 arm)
        n = len(expected["nand_roles"])
        planted += n
        surfaced += min(n, schema.W4_MAX_CONFLICTS)
    assert planted and surfaced < planted
    cap_bound = surfaced / planted
    assert cap_bound < 1.0
    assert cap_bound >= schema.NAND_RECOVERY_FLOOR  # the floor is the cap bound
    assert pytest.approx(0.82) == schema.NAND_RECOVERY_FLOOR


def test_standing_bars_are_corpus_scoped_and_can_fail():
    """The adversarial lane arms three extra bars; the v1 lane is unchanged;
    and each adversarial bar FAILS on a degraded metric snapshot (a bar that
    cannot fail is not a gate)."""
    v1_bars = {m for m, _, _, _ in schema.standing_bars(corpus.DEFAULT_CORPUS)}
    adv_bars = {m for m, _, _, _ in schema.standing_bars(corpus.ADVERSARIAL_CORPUS)}
    # v1 arms the three E2E-7 floors (sufficiency is MEASURED, not gated).
    assert v1_bars == {
        "conflict_surfacing_rate",
        "dig_deeper_navigation_accuracy",
        "false_positive_rate",
    }
    assert v1_bars <= set(schema.METRIC_VALUES)
    assert adv_bars - v1_bars == {
        "nand_recovery_rate",
        "high_severity_nand_recovery",
        "contested_classification_accuracy",
    }
    at_bars = {m: 1.0 for m in schema.metric_values(corpus.ADVERSARIAL_CORPUS)}
    at_bars["false_positive_rate"] = 0.0
    at_bars["nand_recovery_rate"] = 0.8261
    assert schema.tripped_bars(at_bars, corpus.ADVERSARIAL_CORPUS) == []
    for metric, degraded in (
        ("nand_recovery_rate", 0.65),  # a cap regression / dropped NANDs
        ("high_severity_nand_recovery", 0.0),  # unsorted selection
        ("contested_classification_accuracy", 0.8696),  # `>=` threshold
        ("dig_deeper_navigation_accuracy", 0.7778),  # naive pointer choice
    ):
        broken = dict(at_bars)
        broken[metric] = degraded
        assert schema.tripped_bars(broken, corpus.ADVERSARIAL_CORPUS), metric
    # The v1 vocabulary has no adversarial arms at all.
    assert schema.tripped_bars(at_bars, corpus.DEFAULT_CORPUS) == []


def test_tripped_bars_reads_an_absent_metric_as_tripped():
    """An ABSENT (or non-numeric) metric is a TRIPPED bar, never a met one.

    The first version `continue`d on a missing value, so `tripped_bars({})`
    returned `[]` — an empty or partial snapshot read as "every bar met" on the
    helper the lane's own gate assertions call. Fail-open on a gate helper, and
    the OPPOSITE of `aggregate_metrics`' stated convention (an empty denominator
    collapses to the WORST value, so a corpus that stops measuring an arm reads
    as a failure). Mutation check: restoring the `continue` makes every arm
    here return `[]`.
    """
    adv = corpus.ADVERSARIAL_CORPUS
    everything = [label for _, _, _, label in schema.standing_bars(adv)]
    assert len(everything) == 6, everything  # 3 v1 bars + 3 adversarial arms
    # Empty snapshot: every armed bar is tripped, on BOTH corpora.
    assert schema.tripped_bars({}, adv) == everything
    assert schema.tripped_bars({}, corpus.DEFAULT_CORPUS) == everything[:3]
    # Each bar removed on its own trips exactly that bar and nothing else — a
    # partial snapshot cannot pass by omission.
    complete = {m: 1.0 for m in schema.metric_values(adv)}
    complete["false_positive_rate"] = 0.0
    complete["nand_recovery_rate"] = 0.8261
    assert schema.tripped_bars(complete, adv) == []
    for metric, _, _, label in schema.standing_bars(adv):
        partial = {k: v for k, v in complete.items() if k != metric}
        assert schema.tripped_bars(partial, adv) == [label], metric
    # A non-numeric value (and a bool, which `isinstance(x, int)` accepts) is
    # not a measurement either.
    for bad in (None, "0.99", True, [0.99]):
        corrupted = dict(complete)
        corrupted["conflict_surfacing_rate"] = bad
        assert schema.tripped_bars(corrupted, adv) == ["conflict-surfacing-below-floor"], bad


def test_failure_classes_record_every_tripped_bar_not_the_first_four():
    """A bless records EVERY tripped bar in `history[]`, never a `[:4]` slice.

    The slice was exactly lossless while v1 was the only corpus (3 bars +
    `a4-not-measured` = 4) and became lossy when #2275 armed three more: a
    `why_suite_adversarial` re-publish with 6 tripped bars wrote a blessed entry
    naming only four, with nothing recording the drop.
    Mutation check: restoring `failure_classes[:4]` in `runner._bless_main` — or
    the equivalent slice in `failure_classes_for` — fails the length assert.
    """
    adv = corpus.ADVERSARIAL_CORPUS
    broken = {m: 1.0 for m in schema.metric_values(adv)}
    broken.update(
        {
            "conflict_surfacing_rate": 0.5,
            "dig_deeper_navigation_accuracy": 0.5,
            "false_positive_rate": 0.5,
            "nand_recovery_rate": 0.5,
            "high_severity_nand_recovery": 0.0,
            "contested_classification_accuracy": 0.5,
        }
    )
    assert len(schema.tripped_bars(broken, adv)) == 6  # every armed bar
    classes = runner.failure_classes_for(broken, adv)
    assert classes == schema.tripped_bars(broken, adv)
    assert len(classes) == 6, classes
    # v1 keeps its own composition: bars + the `a4-not-measured` marker, still
    # complete (4 = 3 bars + the marker — the case the old cap was sized for).
    assert runner.failure_classes_for(broken, corpus.DEFAULT_CORPUS, a4_measured=False) == [
        "conflict-surfacing-below-floor",
        "dig-deeper-navigation-below-floor",
        "clean-false-positives",
        "a4-not-measured",
    ]
    assert runner.failure_classes_for(broken, corpus.DEFAULT_CORPUS) == [
        "conflict-surfacing-below-floor",
        "dig-deeper-navigation-below-floor",
        "clean-false-positives",
    ]  # a MEASURED A4 is not a failure class


def test_bless_writes_every_failure_class_into_the_history_record(tmp_path, monkeypatch):
    """The BLESS PATH — not just the helper — records all six classes.

    `failure_classes[:4]` lived at the `_bless_main` call site, so a helper-only
    assertion cannot fail on the pre-fix code. This drives `_bless_main` itself
    (with the run and the receipt plumbing stubbed) and reads the baseline it
    writes: pre-fix, `history[-1].failure_classes` has FOUR entries and this
    asserts six.
    """
    adv = corpus.ADVERSARIAL_CORPUS
    pending = corpus.load_baseline(corpus.WHY_DIR, posture="m2", corpus=adv)
    metrics = {m: 1.0 for m in schema.metric_values(adv)}
    metrics.update(
        {
            "conflict_surfacing_rate": 0.5,
            "dig_deeper_navigation_accuracy": 0.5,
            "false_positive_rate": 0.5,
            "nand_recovery_rate": 0.5,
            "high_severity_nand_recovery": 0.0,
            "contested_classification_accuracy": 0.5,
        }
    )
    report = {
        "run_status": "completed",
        "run_id": "w3b-bless-test",
        "date": "2026-11-09",
        "metrics": metrics,
        "corpus_hash": pending["fixtures_hash"],
        "judge_pin": judge.judge_pin(),
        "resolved_config": pending["config"],
        "a4_result": {"measured": True},
        "log": [],
    }
    written = tmp_path / "adversarial_m2.json"
    monkeypatch.setattr(runner, "run_benchmark", lambda **_: report)
    monkeypatch.setattr(
        runner, "corpus",
        type("C", (), {
            "DEFAULT_CORPUS": corpus.DEFAULT_CORPUS,
            "RECEIPTS_DIR": tmp_path / "receipts",
            "baseline_path": staticmethod(lambda *a, **k: written),
            "load_baseline": staticmethod(lambda *a, **k: pending),
        }),
    )
    monkeypatch.setattr(runner, "build_receipt", lambda *a, **k: {})
    monkeypatch.setattr(runner, "validate_receipt", lambda *a, **k: [])

    rc = runner._bless_main(tmp_path, "m2", "test justification", None, False, False, [], adv)
    assert rc == 0
    blessed = json.loads(written.read_text(encoding="utf-8"))
    recorded = blessed["history"][-1]["failure_classes"]
    assert len(recorded) == 6, recorded
    assert sorted(recorded) == sorted(schema.tripped_bars(metrics, adv))


def _receipt(corpus_name: str, metrics: dict) -> dict:
    """A minimal COMPLETED report run through `build_receipt`.

    The receipt CONTRACT is what is under test here, so it is exercised
    without a benchmark run (the lane tests cover the run-level numbers).
    """
    return runner.build_receipt(
        {
            "run_id": "r-receipt-contract",
            "date": "2026-11-09",
            "run_status": "completed",
            "verdict": schema.VERDICT_INCONCLUSIVE,
            "failure_origin": None,
            "commit": "d16ea7358217",
            "corpus_hash": "sha256:" + "a" * 64,
            "judge_pin": judge.judge_pin(),
            "resolved_config": {"corpus": corpus_name},
            "cost_usd": 0.0,
            "metrics": metrics,
            "point_results": [{"point_id": "p1"}],
        }
    )


def test_validate_receipt_checks_the_CORPUS_metric_vocabulary():
    """`validate_receipt` must require the corpus's own metric set, not the v1 five.

    PRE-FIX (fails this test): the completeness check used
    `schema.METRIC_VALUES` — the v1 FIVE — so an adversarial receipt that had
    lost `nand_recovery_rate`, `high_severity_nand_recovery` and
    `contested_classification_accuracy` validated CLEAN: a subset check is a
    fail-open on an evidentiality artifact, the same shape
    `schema.tripped_bars` closed one file over.

    Mutation check: restoring `schema.METRIC_VALUES` in
    `runner.validate_receipt`, or "fixing" it with `schema.corpus_of(receipt)`
    (which reads `config`, not `resolved_config`, and therefore resolves to the
    default corpus), fails the three adversarial asserts below.
    """
    adv = corpus.ADVERSARIAL_CORPUS
    v1 = corpus.DEFAULT_CORPUS
    # Complete receipts are still clean on BOTH lanes — this is not a
    # stricter-than-spec rejection of the v1 five or the adversarial eight.
    assert runner.validate_receipt(
        _receipt(adv, {m: 1.0 for m in schema.metric_values(adv)})
    ) == []
    assert runner.validate_receipt(
        _receipt(v1, {m: 1.0 for m in schema.metric_values(v1)})
    ) == []
    # 🔑 The discriminating case: each ADVERSARIAL-ONLY arm is required.
    adv_only = sorted(schema.ADVERSARIAL_METRIC_VALUES - schema.METRIC_VALUES)
    assert adv_only == [
        "contested_classification_accuracy",
        "high_severity_nand_recovery",
        "nand_recovery_rate",
    ]
    for missing_metric in adv_only:
        metrics = {m: 1.0 for m in schema.metric_values(adv)}
        del metrics[missing_metric]
        issues = runner.validate_receipt(_receipt(adv, metrics))
        assert issues, f"{missing_metric} missing from an adversarial receipt must be an issue"
        assert missing_metric in issues[0], (missing_metric, issues)
    # The v1 five are REQUIRED on the adversarial lane too — they are graded
    # there, so the corpus scope widens the set, it does not swap it.
    metrics = {m: 1.0 for m in schema.metric_values(adv)}
    del metrics["support_chain_sufficiency"]
    assert runner.validate_receipt(_receipt(adv, metrics))
    # ... and the v1 lane keeps rejecting a missing v1 metric (unchanged).
    metrics = {m: 1.0 for m in schema.metric_values(v1)}
    del metrics["false_positive_rate"]
    assert runner.validate_receipt(_receipt(v1, metrics))


def test_aggregate_metrics_adversarial_arms_math():
    rows = [
        {  # live 3-NAND cluster: cap recovers 2, high one recovered
            "expected_conflict": True,
            "nand_planted": 3,
            "nand_recovered": 2,
            "over_cap_cluster": True,
            "high_severity_recovered": True,
            "expected_contested": True,
            "reads_contested": True,
            "nav_correct": 2,
            "nav_total": 2,
            "support_sufficient": True,
            "tradeoff_sufficient": None,
            "clean": False,
            "false_positive": False,
        },
        {  # live 1-NAND point, fully recovered, at-threshold (not contested)
            "expected_conflict": False,
            "nand_planted": 1,
            "nand_recovered": 1,
            "over_cap_cluster": False,
            "high_severity_recovered": None,
            "expected_contested": False,
            "reads_contested": False,
            "nav_correct": 2,
            "nav_total": 2,
            "support_sufficient": True,
            "tradeoff_sufficient": None,
            "clean": False,
            "false_positive": False,
        },
        {  # clean control
            "expected_conflict": False,
            "nand_planted": 0,
            "nand_recovered": 0,
            "over_cap_cluster": False,
            "high_severity_recovered": None,
            "expected_contested": False,
            "reads_contested": False,
            "nav_correct": 1,
            "nav_total": 1,
            "support_sufficient": True,
            "tradeoff_sufficient": None,
            "clean": True,
            "false_positive": False,
        },
    ]
    metrics = schema.aggregate_metrics(rows, corpus=corpus.ADVERSARIAL_CORPUS)
    assert metrics["nand_recovery_rate"] == pytest.approx(3 / 4)
    assert metrics["high_severity_nand_recovery"] == 1.0
    assert metrics["contested_classification_accuracy"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    # The v1 vocabulary does not gain the arms (a v1 baseline snapshot would
    # otherwise fail its own validation).
    assert "nand_recovery_rate" not in schema.aggregate_metrics(rows)
    # Empty denominators collapse to the WORST value, never a clean pass.
    empty = schema.aggregate_metrics(
        [{"clean": True, "false_positive": False}], corpus=corpus.ADVERSARIAL_CORPUS
    )
    assert empty["nand_recovery_rate"] == 0.0
    assert empty["high_severity_nand_recovery"] == 0.0
    assert empty["contested_classification_accuracy"] == 0.0


# ── The naive probe (hermetic half of the discrimination proof) ───────────


def _block(kinds: list[str], *, nands: int, variance: float) -> dict:
    return {
        "point_id": "p",
        "support_chain": [{"point_id": "s", "content_snippet": "s", "weight": 0.9}],
        "ep": {"variance": variance, "has_ep": True, "contested": variance > 0.04},
        "conflicts": {
            "contested": variance > 0.04,
            "nands": [{"point_id": f"n{i}", "content_snippet": f"n{i}"} for i in range(nands)],
        },
        "dig_deeper": [{"kind": k, "label": k, "target": f"t-{k}"} for k in kinds],
    }


def test_naive_degradation_is_mechanical_and_targets_each_arm():
    role_map = {
        "counter_a": "n0",
        "counter_b": "n1",
        "counter_c": "n2",
        "successor": "succ",
        "grand_successor": "grand",
        "option_a": "opt-a",
        "option_c": "opt-c",
    }
    # multi_nand: insertion order (n0, n1) replaces the severity selection and
    # the nand pointer follows the first planted one.
    naive = adversarial.degrade_to_naive(
        _block(["supports", "nand"], nands=2, variance=0.05), {}, role_map, "multi_nand"
    )
    assert [n["point_id"] for n in naive["conflicts"]["nands"]] == ["n0", "n1"]
    assert next(p for p in naive["dig_deeper"] if p["kind"] == "nand")["target"] == "n0"
    # boundary: variance == threshold reads as contested under `>=`.
    naive = adversarial.degrade_to_naive(
        _block(["supports", "nand"], nands=1, variance=0.04), {}, role_map, "boundary"
    )
    assert naive["ep"]["contested"] is True
    assert naive["conflicts"]["contested"] is True
    # chain: the DEEPEST successor instead of the immediate one.
    naive = adversarial.degrade_to_naive(
        _block(["supports", "nand", "superseded"], nands=1, variance=0.05),
        {},
        role_map,
        "chain",
    )
    assert next(p for p in naive["dig_deeper"] if p["kind"] == "superseded")["target"] == "grand"
    # wide_decision: the FIRST alternative instead of the EP-favored one.
    naive = adversarial.degrade_to_naive(
        _block(["supports", "nand", "tradeoff"], nands=1, variance=0.05),
        {},
        role_map,
        "wide_decision",
    )
    assert next(p for p in naive["dig_deeper"] if p["kind"] == "tradeoff")["target"] == "opt-a"


def test_readme_documents_what_each_corpus_can_and_cannot_falsify():
    """The acceptance criterion: the README names BOTH corpora and states the
    limits of each (the v1 note must no longer point at #2275 as open work)."""
    text = (corpus.WHY_DIR / "README.md").read_text(encoding="utf-8")
    assert "adversarial" in text.lower()
    assert "why_suite_adversarial" in text
    assert "can falsify" in text.lower()
    assert "cannot falsify" in text.lower()
    # The v1 limitations note must point at the corpus that now closes it.
    assert "is open work (issue #2275" not in text
    # ...and it must not OVERCLAIM closure: a gap that has no family in EITHER
    # corpus must be declared unclosed, never absorbed into the v1 note's
    # "those are exactly the adversarial families" pointer (#2275 review).
    # Whitespace-normalised: the sentence wraps across two lines, so a raw
    # substring check would pass vacuously.
    flat = " ".join(text.lower().split())
    assert "those are exactly the adversarial corpus's families" not in flat
    adv_limit = text.split("**`why_suite_adversarial` CANNOT falsify:**", 1)
    assert len(adv_limit) == 2, "the adversarial corpus must declare its limits"
    adv_cannot = adv_limit[1].split("\n\n", 1)[0].lower()
    # malformed-surface tolerance is named by the v1 list but is NOT one of
    # the adversarial families — claiming it closed would stop a reader from
    # looking for the gap.
    assert "malformed-surface" in adv_cannot
    assert "real-memory" in adv_cannot


def test_manifest_of_self_manifest_is_registered():
    """The adversarial fixture files are covered by a hashed self-manifest —
    an unlisted file would ride outside ``fixtures_hash``."""
    for name in (corpus.DEFAULT_CORPUS, corpus.ADVERSARIAL_CORPUS):
        profile = corpus.PROFILES[name]
        doc = json.loads((corpus.WHY_DIR / profile.self_manifest_file).read_text())
        assert set(doc["files"]) == {
            p.relative_to(corpus.WHY_DIR).as_posix()
            for p in corpus.corpus_file_paths(corpus=name)
        }
        assert doc["corpus"] == name
        assert doc["fixtures_hash"] == corpus.compute_fixtures_hash(corpus=name)
