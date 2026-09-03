"""W2 write-path corpus contract tests (issue #2097, W2-a) — surfaces S4 + S15.

Hermetic contract tests for the planted-gold corpus + schemas (plan
DM-3/4/5): every committed fixture/gold/baseline validates; the answer key
never lives in fixtures (a ``gold`` key is a validation error); the
``fixtures_hash`` covers fixture AND gold files (a gold-only edit ⇒ hash
mismatch ⇒ compare verdict ``inconclusive``); the baseline schema enforces
justification-to-bless + config-mismatch ⇒ inconclusive; regeneration is
byte-idempotent (fix-wave protocol); corpus floors (≥ 4 sessions, ≥ 60
planted salient units with verbatim anchors) hold for stable E2E-2
denominators; ``survival`` is defined at the POINT level with
REPHRASE-linked acceptance (the point-level unit-of-analysis rule — per
research-brief/plan, NOT eval-spec §5's loopy-NAND "A1") per
``docs/epistemic-layer-eval-spec.md`` §P5.

Pure unit/contract layer — no DB, no network, no LLM (test-design #2093 S4).
"""
from __future__ import annotations

import copy
import json

import pytest

from tests.eval.write_path import corpus, generate_corpus, schema

COMMITTED = corpus.WRITE_PATH_DIR
COMMITTED_SESSIONS = corpus.session_ids()
COMMITTED_GOLDS = [p for p in corpus.corpus_file_paths() if ".gold.json" in p.name]
COMMITTED_FIXTURES = [p for p in corpus.corpus_file_paths() if ".gold.json" not in p.name]

MIN_SESSIONS = 4
MIN_PLANTED_SALIENT_UNITS = 60


# ── Committed corpus validity (S4: every fixture validates; fixture ↔ gold) ─


@pytest.mark.parametrize("session_id", COMMITTED_SESSIONS)
def test_committed_fixture_validates(session_id: str) -> None:
    fixture = corpus.load_fixture(session_id)
    issues = schema.validate_fixture(fixture)
    assert issues == [], f"fixture {session_id} failed validation: {issues}"
    # DM-3: adapter-visible fields ONLY.
    assert set(fixture) == {"session_id", "harness", "conversation"}


@pytest.mark.parametrize("session_id", COMMITTED_SESSIONS)
def test_committed_gold_validates_against_fixture(session_id: str) -> None:
    fixture = corpus.load_fixture(session_id)
    gold = corpus.load_gold(session_id)
    issues = schema.validate_gold(gold, fixture=fixture)
    assert issues == [], f"gold {session_id} failed validation: {issues}"


def test_committed_baseline_validates_as_first_run_pending() -> None:
    baseline = corpus.load_baseline()
    issues = schema.validate_baseline(baseline)
    assert issues == [], f"baseline failed validation: {issues}"
    # Benchmark-first decision: no preset quality bar — targets come from
    # first-run data, so the committed baseline carries no metrics yet.
    assert baseline["metrics"] == {}
    assert baseline["history"] == []
    assert baseline["justification"] is None
    assert baseline["judge_pin"] is None


def test_generator_full_validation_is_clean() -> None:
    """The committed dir passes the generator's own full validation (no drift)."""
    assert generate_corpus.validate_committed() == []
    assert generate_corpus.check_drift() == []


# ── Answer-key discipline: a gold key inside a fixture is a VALIDATION ERROR ─


def test_gold_key_in_fixture_is_validation_error() -> None:
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    poisoned = copy.deepcopy(fixture)
    poisoned["gold"] = {"planted_units": [{"id": "u_x", "verbatim_anchor": "secret"}]}
    issues = schema.validate_fixture(poisoned)
    assert any("gold" in issue for issue in issues), issues
    assert any("VALIDATION ERROR" in issue for issue in issues), issues


def test_gold_key_in_fixture_turn_is_validation_error() -> None:
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    poisoned = copy.deepcopy(fixture)
    poisoned["conversation"][0]["gold"] = "leaked answer key"
    issues = schema.validate_fixture(poisoned)
    assert any("conversation[0]" in issue and "gold" in issue for issue in issues), issues


def test_unknown_fixture_keys_rejected() -> None:
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    poisoned = copy.deepcopy(fixture)
    poisoned["verbatim_anchor"] = "answer key must not ride in the fixture"
    issues = schema.validate_fixture(poisoned)
    assert any("verbatim_anchor" in issue for issue in issues), issues


def test_unknown_turn_keys_rejected() -> None:
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    poisoned = copy.deepcopy(fixture)
    poisoned["conversation"][0]["speaker_id"] = "ghost"
    issues = schema.validate_fixture(poisoned)
    assert any("conversation[0]" in issue and "speaker_id" in issue for issue in issues), issues


# ── Gold invariants ─────────────────────────────────────────────────────────


def test_planted_unit_anchor_must_be_grounded_in_planted_turn() -> None:
    """fixture/gold drift (anchor not in the planted turn) is a validation error."""
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    gold = copy.deepcopy(corpus.load_gold(COMMITTED_SESSIONS[0]))
    gold["planted_units"][0]["verbatim_anchor"] = "a phrase that never appears anywhere"
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("not a normalized substring" in issue for issue in issues), issues


def test_depth_bucket_must_match_planted_turn() -> None:
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    gold = copy.deepcopy(corpus.load_gold(COMMITTED_SESSIONS[0]))
    unit = gold["planted_units"][0]
    unit["depth_bucket"] = "late"  # flip whatever the derived bucket is
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("depth_bucket" in issue and "inconsistent" in issue for issue in issues), issues


def test_salient_units_are_11_with_planted_units() -> None:
    for session_id in COMMITTED_SESSIONS:
        gold = corpus.load_gold(session_id)
        planted = {u["id"] for u in gold["planted_units"]}
        salient = {u["id"] for u in gold["salient_units"]}
        assert planted == salient, (
            f"{session_id}: salient set must equal planted set "
            f"(missing={planted - salient}, extra={salient - planted})"
        )
    # And a gold missing a survival entry for a planted unit is invalid.
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    gold = copy.deepcopy(corpus.load_gold(COMMITTED_SESSIONS[0]))
    gold["salient_units"].pop()
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("missing survival semantics" in issue for issue in issues), issues


def test_distractor_leakage_tolerance_is_one() -> None:
    for session_id in COMMITTED_SESSIONS:
        gold = corpus.load_gold(session_id)
        assert gold["distractor_leakage_tolerance"] == 1


def test_hazard_quotes_ground_and_carry_sources() -> None:
    for session_id in COMMITTED_SESSIONS:
        fixture = corpus.load_fixture(session_id)
        gold = corpus.load_gold(session_id)
        for hazard in gold["attribution_hazards"]:
            assert hazard["source"]
            turn = fixture["conversation"][hazard["planted_turn"] - 1]["content"]
            assert schema.anchor_present(hazard["quote"], turn), (
                f"{session_id}: hazard {hazard['id']} quote ungrounded"
            )


def test_point_level_survival_semantics_present() -> None:
    """S15: survival is defined at the POINT level (point-level unit-of-analysis
    rule — REPHRASE-linked accepted), never page-level; anchors are the
    survival predicate."""
    for session_id in COMMITTED_SESSIONS:
        gold = corpus.load_gold(session_id)
        for entry in gold["salient_units"]:
            survival = entry["survival"]
            assert survival["via_anchor"], f"{session_id}: via_anchor empty for {entry['id']}"
            assert isinstance(survival["accepts_rephrase_linked"], bool)
            assert isinstance(survival["provenance_required"], bool)
            assert isinstance(survival["ep_update_required"], bool)
            matching = [u for u in gold["planted_units"] if u["id"] == entry["id"]]
            assert matching and survival["via_anchor"] == matching[0]["verbatim_anchor"]
    # REPHRASE-linked acceptance is exercised, not uniform-true.
    accepts = {
        entry["survival"]["accepts_rephrase_linked"]
        for session_id in COMMITTED_SESSIONS
        for entry in corpus.load_gold(session_id)["salient_units"]
    }
    assert accepts == {True, False}, "corpus must exercise both survival paths (REPRHASE-linked + verbatim)"


# ── fixtures_hash covers fixture AND gold (E2E-2 negative: gold-only edit) ──


def test_fixtures_hash_covers_fixture_and_gold_files() -> None:
    manifest = corpus.load_manifest()
    covered = set(manifest["files"])
    on_disk = {p.relative_to(COMMITTED).as_posix() for p in corpus.corpus_file_paths()}
    assert covered == on_disk, f"manifest coverage mismatch: {covered ^ on_disk}"
    assert manifest["fixtures_hash"] == corpus.compute_fixtures_hash()
    # The committed baseline pins the SAME hash — a gold-only edit invalidates it.
    baseline = corpus.load_baseline()
    assert baseline["fixtures_hash"] == manifest["fixtures_hash"]


def test_gold_only_edit_breaks_manifest_and_fixtures_hash(tmp_path) -> None:
    """A gold-only edit (no fixture change) ⇒ manifest mismatch + hash change
    ⇒ compare verdict inconclusive — never a rubber-stamp (E2E-2 negative)."""
    generate_corpus.write_corpus(root=tmp_path)
    assert corpus.verify_manifest(tmp_path)["ok"]

    gold_file = tmp_path / f"gold/{COMMITTED_SESSIONS[0]}.gold.json"
    gold = json.loads(gold_file.read_text())
    gold["planted_units"][0]["verbatim_anchor"] = "silent answer-key edit"
    gold_file.write_text(json.dumps(gold, indent=2, sort_keys=True) + "\n")

    verification = corpus.verify_manifest(tmp_path)
    assert not verification["ok"]
    assert verification["mismatched"] == [f"gold/{COMMITTED_SESSIONS[0]}.gold.json"]
    # Baseline pins the old hash → a compare against the edited corpus is
    # inconclusive (not pass, not regression).
    baseline = corpus.load_baseline(tmp_path)
    verdict = schema.compare_run(
        run_metrics={"salient_unit_survival_macro": 0.9},
        baseline=baseline,
        resolved_config=baseline["config"],
        run_fixtures_hash=corpus.compute_fixtures_hash(tmp_path),
    )
    assert verdict == schema.VERDICT_INCONCLUSIVE


def test_fixture_only_edit_also_breaks_fixtures_hash(tmp_path) -> None:
    """Both fixture and gold files are in hash scope — fixtures edits are not
    silent either (they require corpus-bless regeneration)."""
    generate_corpus.write_corpus(root=tmp_path)
    fixture_file = tmp_path / f"fixtures/{COMMITTED_SESSIONS[0]}.json"
    fixture = json.loads(fixture_file.read_text())
    fixture["conversation"][-1]["content"] += " extra trailing words"
    fixture_file.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    verification = corpus.verify_manifest(tmp_path)
    assert not verification["ok"]
    assert verification["mismatched"] == [f"fixtures/{COMMITTED_SESSIONS[0]}.json"]


# ── Baseline schema: justification-to-bless + config-mismatch ⇒ inconclusive ─


def _sample_published_baseline() -> dict:
    baseline = copy.deepcopy(corpus.load_baseline())
    baseline["judge_pin"] = "judge-write-path-v1"
    baseline["metrics"] = {
        "salient_unit_survival_macro": 0.83,
        "salient_unit_survival_strict": 0.78,
        "distractor_leakage_per_run": 1,
        "sessions_emitting": 1.0,
        "quote_fidelity": 0.84,
        "provenance_accuracy": 0.86,
    }
    baseline["history"] = [
        {
            "date": "2026-09-05",
            "values": baseline["metrics"],
            "failure_classes": ["first published baseline (bad number, per protocol)"],
            "justification": "first published baseline — targets set from first-run data",
        }
    ]
    baseline["justification"] = "first published baseline — targets set from first-run data"
    return baseline


def test_published_baseline_requires_judge_pin() -> None:
    baseline = _sample_published_baseline()
    baseline["judge_pin"] = None
    issues = schema.validate_baseline(baseline)
    assert any("judge_pin" in issue for issue in issues), issues


def test_published_baseline_requires_justification() -> None:
    baseline = _sample_published_baseline()
    baseline["justification"] = None
    issues = schema.validate_baseline(baseline)
    assert any("justification" in issue and "requires" in issue for issue in issues), issues


def test_pending_baseline_cannot_carry_justification() -> None:
    baseline = copy.deepcopy(corpus.load_baseline())
    baseline["justification"] = "nothing has been blessed yet"
    issues = schema.validate_baseline(baseline)
    assert any("justification" in issue and "first-run-pending" in issue for issue in issues), issues


def test_unknown_metric_key_rejected() -> None:
    baseline = _sample_published_baseline()
    baseline["metrics"]["salient_units_per_page"] = 1.0  # page-level vocab is NOT a metric
    issues = schema.validate_baseline(baseline)
    assert any("unknown metric" in issue for issue in issues), issues


def test_compare_verdict_vocabulary() -> None:
    baseline = _sample_published_baseline()
    config = baseline["config"]
    better = {
        "salient_unit_survival_macro": 0.9,
        "salient_unit_survival_strict": 0.85,
        "distractor_leakage_per_run": 0,
        "sessions_emitting": 1.0,
        "quote_fidelity": 0.9,
        "provenance_accuracy": 0.9,
    }
    worse = dict(better)
    worse["salient_unit_survival_macro"] = 0.5

    assert (
        schema.compare_run(better, baseline, resolved_config=config, run_fixtures_hash=baseline["fixtures_hash"])
        == schema.VERDICT_PASS
    )
    assert (
        schema.compare_run(worse, baseline, resolved_config=config, run_fixtures_hash=baseline["fixtures_hash"])
        == schema.VERDICT_REGRESSION
    )
    # Leakage is lower-better: a higher leakage run is a regression.
    leaky = dict(better)
    leaky["distractor_leakage_per_run"] = 3
    assert (
        schema.compare_run(leaky, baseline, resolved_config=config, run_fixtures_hash=baseline["fixtures_hash"])
        == schema.VERDICT_REGRESSION
    )


def test_pending_baseline_cannot_pin_judge() -> None:
    baseline = copy.deepcopy(corpus.load_baseline())
    baseline["judge_pin"] = "judge-write-path-v1"  # nothing published yet
    issues = schema.validate_baseline(baseline)
    assert any("judge_pin" in issue and "first-run-pending" in issue for issue in issues), issues


def test_baseline_metric_values_are_typed_and_ranged() -> None:
    def _with_metrics(mutator) -> list[str]:
        baseline = _sample_published_baseline()
        mutator(baseline["metrics"])
        return schema.validate_baseline(baseline)

    # Fraction out of range.
    assert any("fraction in [0, 1]" in issue
               for issue in _with_metrics(lambda m: m.__setitem__("salient_unit_survival_macro", 1.7)))
    # Non-numeric value.
    assert any("expected a number" in issue
               for issue in _with_metrics(lambda m: m.__setitem__("sessions_emitting", "1.0")))
    # Bool is not a number here.
    assert any("expected a number" in issue
               for issue in _with_metrics(lambda m: m.__setitem__("quote_fidelity", True)))
    # Leakage above the gold-locked tolerance.
    assert any("tolerance" in issue
               for issue in _with_metrics(lambda m: m.__setitem__("distractor_leakage_per_run", 5)))
    assert any("non-negative int" in issue
               for issue in _with_metrics(lambda m: m.__setitem__("distractor_leakage_per_run", "1")))
    # In-range values stay clean.
    assert _with_metrics(lambda m: None) == []


def test_history_entries_all_shape_validated() -> None:
    def _with_history(mutator) -> list[str]:
        baseline = _sample_published_baseline()
        mutator(baseline["history"])
        return schema.validate_baseline(baseline)

    # An older entry missing its justification is flagged (not just the newest).
    def _drop_justification(history) -> None:
        history[0]["justification"] = None

    assert any("history[0]" in issue and "justification" in issue
               for issue in _with_history(_drop_justification))
    # Non-string failure class.
    def _bad_failure_classes(history) -> None:
        history[0]["failure_classes"] = ["a", 3]

    assert any("failure_classes" in issue for issue in _with_history(_bad_failure_classes))
    # Verdict vocabulary.
    def _bad_verdict(history) -> None:
        history[0]["verdict"] = "inconclusive"

    assert any("verdict" in issue for issue in _with_history(_bad_verdict))


def test_compare_config_mismatch_is_inconclusive() -> None:
    """Config mismatch (resolved_config ≠ baseline config) ⇒ inconclusive —
    never a rubber-stamp (plan §4.3.3 compare semantics)."""
    baseline = _sample_published_baseline()
    config = dict(baseline["config"])
    config["mode"] = "full"
    assert (
        schema.compare_run(
            run_metrics={"salient_unit_survival_macro": 0.9},
            baseline=baseline,
            resolved_config=config,
            run_fixtures_hash=baseline["fixtures_hash"],
        )
        == schema.VERDICT_INCONCLUSIVE
    )
    # fixtures_hash mismatch is also inconclusive even with identical config.
    assert (
        schema.compare_run(
            run_metrics={"salient_unit_survival_macro": 0.9},
            baseline=baseline,
            resolved_config=baseline["config"],
            run_fixtures_hash="sha256:deadbeef",
        )
        == schema.VERDICT_INCONCLUSIVE
    )


def test_compare_against_pending_baseline_is_inconclusive() -> None:
    """Benchmark-first: the committed pending baseline has no targets, so the
    first-run compare cannot pass or regress — it is inconclusive until W2-b
    publishes the first baseline."""
    pending = corpus.load_baseline()
    verdict = schema.compare_run(
        run_metrics={},
        baseline=pending,
        resolved_config=pending["config"],
        run_fixtures_hash=pending["fixtures_hash"],
    )
    assert verdict == schema.VERDICT_INCONCLUSIVE


def test_first_publish_bless_from_pending_baseline() -> None:
    """The documented benchmark-first flow must be implementable: W2-b blesses
    the first (expected-bad) run against the committed first-run-pending
    baseline — no committed targets exist to compare against, so the compare is
    skipped and the run's metrics become the first committed targets."""
    pending = corpus.load_baseline()
    run = {
        "fixtures_hash": pending["fixtures_hash"],
        "judge_pin": "judge-write-path-v1",
        "config": pending["config"],
        "date": "2026-09-05",
        "metrics": {
            "salient_unit_survival_macro": 0.5,
            "salient_unit_survival_strict": 0.44,
            "distractor_leakage_per_run": 1,
            "sessions_emitting": 1.0,
            "quote_fidelity": 0.6,
            "provenance_accuracy": 0.7,
        },
        "failure_classes": ["triage misses on buried-signal transcripts"],
    }
    blessed = schema.bless_baseline(
        pending,
        run,
        justification="first published baseline (bad number, per fix-wave protocol)",
    )
    assert schema.validate_baseline(blessed) == []
    assert blessed["metrics"] == run["metrics"]
    assert blessed["judge_pin"] == "judge-write-path-v1"
    assert blessed["justification"]
    assert len(blessed["history"]) == 1
    # First publish has no prior targets — no compare verdict is recorded.
    assert "verdict" not in blessed["history"][0]
    # A first publish without a pinned judge is rejected.
    run_no_pin = dict(run, judge_pin=None)
    with pytest.raises(ValueError, match="judge_pin"):
        schema.bless_baseline(pending, run_no_pin, justification="first baseline")


def test_bless_pass_records_pass_verdict() -> None:
    previous = _sample_published_baseline()
    better = {
        "salient_unit_survival_macro": 0.9,
        "salient_unit_survival_strict": 0.85,
        "distractor_leakage_per_run": 0,
        "sessions_emitting": 1.0,
        "quote_fidelity": 0.9,
        "provenance_accuracy": 0.9,
    }
    run = {
        "fixtures_hash": previous["fixtures_hash"],
        "judge_pin": "judge-write-path-v1",
        "config": previous["config"],
        "date": "2026-09-12",
        "metrics": better,
        "failure_classes": [],
    }
    blessed = schema.bless_baseline(previous, run, justification="fix-wave improved survival")
    assert schema.validate_baseline(blessed) == []
    assert blessed["history"][-1]["verdict"] == schema.VERDICT_PASS
    # First publish records no verdict (no prior targets); the new pass bless does.
    assert "verdict" not in blessed["history"][0]


def test_bless_regression_requires_justification() -> None:
    previous = _sample_published_baseline()
    run = {
        "fixtures_hash": previous["fixtures_hash"],
        "judge_pin": "judge-write-path-v1",
        "config": previous["config"],
        "date": "2026-09-12",
        "metrics": {
            **previous["metrics"],
            # Full vocabulary with macro REGRESSED (0.5 < 0.83 committed) —
            # a partial-metrics bless is now its own error (missing graded
            # dimensions can never be blessed away, review F3).
            "salient_unit_survival_macro": 0.5,
        },
        "failure_classes": ["triage misses on buried-signal transcripts"],
    }
    with pytest.raises(ValueError, match="requires a non-empty justification"):
        schema.bless_baseline(previous, run, justification="")
    with pytest.raises(ValueError, match="requires a non-empty justification"):
        schema.bless_baseline(previous, run, justification="  ")

    blessed = schema.bless_baseline(
        previous, run, justification="benchmarked regression — named failure classes, fix-wave scheduled"
    )
    assert schema.validate_baseline(blessed) == []
    assert blessed["metrics"]["salient_unit_survival_macro"] == 0.5
    assert blessed["justification"]
    assert len(blessed["history"]) == len(previous["history"]) + 1
    assert blessed["history"][-1]["verdict"] == schema.VERDICT_REGRESSION
    assert blessed["history"][-1]["justification"]


def test_via_anchor_desync_is_validation_error() -> None:
    """A salient unit whose survival.via_anchor no longer equals its planted
    verbatim_anchor silently changes the survival predicate the W2 runner
    grades against — the shared validator must reject the desync."""
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    gold = copy.deepcopy(corpus.load_gold(COMMITTED_SESSIONS[0]))
    planted = gold["planted_units"][0]
    salient = next(e for e in gold["salient_units"] if e["id"] == planted["id"])
    # A different phrase that still grounds in the same planted turn (turn 1).
    turn = fixture["conversation"][planted["planted_turn"] - 1]["content"]
    other = "I want to understand why it stopped making progress"
    assert other != planted["verbatim_anchor"]
    assert schema.anchor_present(other, turn)
    salient["survival"]["via_anchor"] = other
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("via_anchor" in issue and "!=" in issue for issue in issues), issues


def test_manifest_shape_validation() -> None:
    manifest = corpus.load_manifest()
    assert schema.validate_manifest(manifest) == []
    malformed = copy.deepcopy(manifest)
    malformed["files"] = ["fixtures/wp01_quarry_debug.json"]  # list, not dict
    assert any("manifest.files" in i for i in schema.validate_manifest(malformed))


def test_verify_manifest_handles_malformed_doc(tmp_path) -> None:
    generate_corpus.write_corpus(root=tmp_path)
    manifest_path = tmp_path / "_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    result = corpus.verify_manifest(tmp_path)
    assert not result["ok"]
    assert result["malformed"] is not None


def test_filename_stem_must_match_embedded_session_id(tmp_path) -> None:
    """Corpus paths are keyed by file stem — a misnamed fixture whose embedded
    session_id disagrees is a validation error (content/path drift)."""
    generate_corpus.write_corpus(root=tmp_path)
    session = COMMITTED_SESSIONS[0]
    fixture_src = tmp_path / f"fixtures/{session}.json"
    gold_src = tmp_path / f"gold/{session}.gold.json"
    # Rename BOTH files to a wrong shared stem — content session_id now
    # disagrees with the path the corpus keying uses.
    wrong_fixture = tmp_path / "fixtures/not_the_right_stem.json"
    wrong_gold = tmp_path / "gold/not_the_right_stem.gold.json"
    wrong_fixture.write_bytes(fixture_src.read_bytes())
    wrong_gold.write_bytes(gold_src.read_bytes())
    fixture_src.unlink()
    gold_src.unlink()
    issues = generate_corpus.validate_committed(root=tmp_path)
    assert any("filename stem" in i and "not_the_right_stem.json" in i for i in issues), issues
    assert generate_corpus.validate_committed() == []  # committed dir unaffected


def test_hazard_sources_are_user_spoken_lines() -> None:
    """Attribution hazards quote content spoken by the named human operator
    (role=user turns) — an assistant-spoken line must not be attributed to a
    human (review round 1 fix for wp01 h_01)."""
    for session_id in COMMITTED_SESSIONS:
        fixture = corpus.load_fixture(session_id)
        gold = corpus.load_gold(session_id)
        for hazard in gold["attribution_hazards"]:
            role = fixture["conversation"][hazard["planted_turn"] - 1]["role"]
            assert role == "user", (
                f"{session_id}: hazard {hazard['id']} quotes an assistant turn "
                f"but names human source {hazard['source']!r}"
            )


def test_hazard_role_enforced_by_shared_validator() -> None:
    """The role==user invariant lives in the shared validator (single source of
    truth), not just the committed-data test — an assistant-spoken hazard is a
    validation error."""
    fixture = corpus.load_fixture(COMMITTED_SESSIONS[0])
    gold = copy.deepcopy(corpus.load_gold(COMMITTED_SESSIONS[0]))
    # Point the first hazard at an ASSISTANT turn with a grounded quote.
    assistant_idx = next(
        i for i, t in enumerate(fixture["conversation"]) if t["role"] == "assistant"
    )
    assistant_content = fixture["conversation"][assistant_idx]["content"]
    probe = assistant_content.split(".")[0]  # grounded quote from the assistant line
    hazard = gold["attribution_hazards"][0]
    hazard["quote"] = probe
    hazard["planted_turn"] = assistant_idx + 1
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("assistant turn" in issue and "hazard" in issue for issue in issues), issues


def test_validator_survives_malformed_fixture_turn_content() -> None:
    """The shared validator must report (never crash on) a malformed fixture
    turn — non-string content, a non-dict turn, or a non-list conversation."""
    fixture = copy.deepcopy(corpus.load_fixture(COMMITTED_SESSIONS[0]))
    gold = copy.deepcopy(corpus.load_gold(COMMITTED_SESSIONS[0]))
    fixture["conversation"][0]["content"] = 12345  # corrupted content
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("not a string" in issue for issue in issues), issues
    # Non-dict turn (crashes a naive .get() cross-check) — corrupt a turn the
    # gold actually references (turn 9: u_05/u_06 + h_01).
    fixture = copy.deepcopy(corpus.load_fixture(COMMITTED_SESSIONS[0]))
    fixture["conversation"][8] = "not-a-dict-turn"
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("not an object" in issue for issue in issues), issues
    # Non-list conversation.
    fixture = copy.deepcopy(corpus.load_fixture(COMMITTED_SESSIONS[0]))
    fixture["conversation"] = "not-a-list"
    issues = schema.validate_gold(gold, fixture=fixture)
    assert any("not a non-empty list" in issue for issue in issues), issues
    # Non-dict planted_units entry (crashes a naive id-set comprehension).
    fixture = copy.deepcopy(corpus.load_fixture(COMMITTED_SESSIONS[0]))
    bad_gold = copy.deepcopy(gold)
    bad_gold["planted_units"][0] = "not-an-object"
    issues = schema.validate_gold(bad_gold, fixture=fixture)
    assert any("not an object" in issue for issue in issues), issues


def test_bless_rejects_invalid_run_metrics() -> None:
    """Run metrics entering the bless boundary are typed/ranged like committed
    metrics — a non-numeric or out-of-range value cannot be blessed (it would
    crash compare_run or bless an impossible target)."""
    previous = _sample_published_baseline()

    def _full() -> dict:
        return dict(previous["metrics"])

    def _run_with(metrics) -> dict:
        return {
            "fixtures_hash": previous["fixtures_hash"],
            "judge_pin": "judge-write-path-v1",
            "config": previous["config"],
            "date": "2026-09-12",
            "metrics": metrics,
            "failure_classes": [],
        }

    bad_string = _full(); bad_string["salient_unit_survival_macro"] = "0.9"
    with pytest.raises(ValueError, match="run metrics are not valid"):
        schema.bless_baseline(previous, _run_with(bad_string), justification="string metric")
    out_of_range = _full(); out_of_range["salient_unit_survival_macro"] = 1.7
    with pytest.raises(ValueError, match="run metrics are not valid"):
        schema.bless_baseline(previous, _run_with(out_of_range), justification="impossible target")
    # First-publish path validates too.
    pending = corpus.load_baseline()
    leaky = _full(); leaky["distractor_leakage_per_run"] = 9
    with pytest.raises(ValueError, match="run metrics are not valid"):
        schema.bless_baseline(pending, _run_with(leaky), justification="over-tolerance leakage")


def test_bless_rejects_partial_metric_vocabulary() -> None:
    """REVIEW-FIX (F3, code-review gate): a published baseline must snapshot
    the FULL graded-metric vocabulary — a partial bless (a lane that failed
    to produce metrics) would permanently drop that lane from the CI-gate
    compare set, silently shrinking the graded surface over time (plan R8:
    no gate degrades to rubber-stamp)."""
    previous = _sample_published_baseline()
    partial = {
        "fixtures_hash": previous["fixtures_hash"],
        "judge_pin": "judge-write-path-v1",
        "config": previous["config"],
        "date": "2026-09-12",
        "metrics": {"salient_unit_survival_macro": 0.5},
        "failure_classes": [],
    }
    with pytest.raises(ValueError, match="missing graded dimensions"):
        schema.bless_baseline(
            previous, partial, justification="partial lane bless"
        )
    # First-publish path also rejects partial snapshots.
    pending = corpus.load_baseline()
    partial["fixtures_hash"] = pending["fixtures_hash"]
    partial["config"] = pending["config"]
    with pytest.raises(ValueError, match="missing graded dimensions"):
        schema.bless_baseline(
            pending, partial, justification="partial first publish"
        )


def test_generator_rejects_out_of_range_planted_turns() -> None:
    import tests.eval.write_path.generate_corpus as gen

    # Distractor with planted_turn 0 would silently negative-index without the guard.
    bad = copy.deepcopy(gen.WP01)
    bad["distractors"][0]["planted_turn"] = 0
    with pytest.raises(ValueError, match="out of range"):
        gen._build_session_docs(bad)
    # Hazard with planted_turn beyond the session length.
    bad2 = copy.deepcopy(gen.WP01)
    bad2["hazards"][0]["planted_turn"] = 999
    with pytest.raises(ValueError, match="out of range"):
        gen._build_session_docs(bad2)


def test_generator_never_clobbers_a_published_baseline(tmp_path) -> None:
    """Once W2-b blesses a published baseline (non-empty metrics), a generator
    re-run must not overwrite it with the first-run-pending render."""
    generate_corpus.write_corpus(root=tmp_path)
    pending = corpus.load_baseline(tmp_path)
    first_run = {
        "fixtures_hash": pending["fixtures_hash"],
        "judge_pin": "judge-write-path-v1",
        "config": pending["config"],
        "date": "2026-09-05",
        "metrics": {
            "salient_unit_survival_macro": 0.5,
            "salient_unit_survival_strict": 0.44,
            "distractor_leakage_per_run": 1,
            "sessions_emitting": 1.0,
            "quote_fidelity": 0.6,
            "provenance_accuracy": 0.7,
        },
        "failure_classes": ["triage misses on buried-signal transcripts"],
    }
    blessed = schema.bless_baseline(
        pending, first_run, justification="first published baseline (bad number, per protocol)"
    )
    baseline_path = tmp_path / "baselines/main.json"
    baseline_path.write_text(json.dumps(blessed, indent=2, sort_keys=True) + "\n")
    generate_corpus.write_corpus(root=tmp_path)  # generator re-run
    after = corpus.load_baseline(tmp_path)
    assert after["metrics"] == blessed["metrics"], "generator clobbered the published baseline"
    assert after["judge_pin"] == "judge-write-path-v1"
    # The frozen corpus itself is still drift-free (baseline excluded from scope).
    assert generate_corpus.check_drift(tmp_path) == []


def test_bless_refuses_inconclusive_compare() -> None:
    previous = _sample_published_baseline()
    run = {
        "fixtures_hash": "sha256:different-corpus",
        "judge_pin": "judge-write-path-v1",
        "config": previous["config"],
        "date": "2026-09-12",
        "metrics": {"salient_unit_survival_macro": 0.9},
        "failure_classes": [],
    }
    with pytest.raises(ValueError, match="inconclusive"):
        schema.bless_baseline(previous, run, justification="cannot bless a mismatched corpus")


def test_first_publish_bless_rejects_corpus_drift() -> None:
    """First publish still enforces the frozen-corpus contract: a run on a
    drifted corpus (fixtures_hash or resolved-config mismatch vs the committed
    pending baseline) cannot be blessed as the first baseline."""
    pending = corpus.load_baseline()
    run = {
        "fixtures_hash": "sha256:drifted-corpus",
        "judge_pin": "judge-write-path-v1",
        "config": pending["config"],
        "date": "2026-09-05",
        "metrics": {"salient_unit_survival_macro": 0.5},
        "failure_classes": [],
    }
    with pytest.raises(ValueError, match="corpus drift"):
        schema.bless_baseline(pending, run, justification="first baseline on drifted corpus")
    run_config_drift = dict(run)
    run_config_drift["fixtures_hash"] = pending["fixtures_hash"]
    run_config_drift["config"] = {**pending["config"], "mode": "full"}
    with pytest.raises(ValueError, match="config"):
        schema.bless_baseline(pending, run_config_drift, justification="first baseline on wrong config")


# ── Regeneration idempotency (fix-wave protocol) + corpus floors ───────────


def test_generator_is_byte_idempotent() -> None:
    """Re-running the generator reproduces the committed frozen corpus exactly
    (fixtures + gold + manifest; the baseline is outside the drift scope by
    design) — the fix-wave guarantee."""
    assert generate_corpus.check_drift() == []
    fresh = generate_corpus.render_corpus()
    for rel, path in generate_corpus._iter_committed(COMMITTED):
        assert path.read_bytes() == fresh[rel], f"{rel} drifted from a fresh render"
    # While the committed baseline is still first-run-pending it must equal the
    # deterministic render (post-publication it is legitimately blessed by W2-b
    # #2098 and no longer equals the fresh render — conditionalize, REVIEW-FIX
    # F4, so this hermetic test survives the first real baseline publish).
    if not (corpus.load_baseline().get("metrics") or {}):
        assert corpus.BASELINE_PATH.read_bytes() == fresh["baselines/main.json"]


def test_corpus_floors_hold() -> None:
    """Issue targets: ≥ 4 fictional sessions; ≥ 60 planted salient units with
    verbatim anchors — stable denominators for E2E-2 percentage assertions."""
    sessions = corpus.session_ids()
    assert len(sessions) >= MIN_SESSIONS
    assert len(COMMITTED_FIXTURES) == len(COMMITTED_GOLDS) == len(sessions)
    total = 0
    for session_id in sessions:
        fixture = corpus.load_fixture(session_id)
        gold = corpus.load_gold(session_id)
        # every planted unit carries a verbatim anchor grounded in its turn
        assert fixture["session_id"] == gold["session_id"] == session_id
        for unit in gold["planted_units"]:
            assert unit["verbatim_anchor"]
            turn = fixture["conversation"][unit["planted_turn"] - 1]["content"]
            assert schema.anchor_present(unit["verbatim_anchor"], turn), unit["id"]
        total += len(gold["planted_units"])
    assert total >= MIN_PLANTED_SALIENT_UNITS
    # ids are file-scoped unique (plan u_101 pattern); assert per-file uniqueness
    for session_id in sessions:
        gold = corpus.load_gold(session_id)
        planted_ids = [u["id"] for u in gold["planted_units"]]
        assert len(planted_ids) == len(set(planted_ids)), f"{session_id}: duplicate unit ids"


def test_corpus_harness_and_kind_coverage() -> None:
    """The corpus exercises ≥ 3 parser seams and all five unit kinds."""
    harnesses = {corpus.load_fixture(s)["harness"] for s in COMMITTED_SESSIONS}
    assert {"codex", "pi", "claude-desktop"} <= harnesses
    kinds = {u["kind"] for s in COMMITTED_SESSIONS for u in corpus.load_gold(s)["planted_units"]}
    assert kinds == {"fact", "idea", "decision", "vibe", "entity"}


def test_normalize_and_anchor_helpers() -> None:
    assert schema.anchor_present("Security review  due May 1", "the security review due May 1 deadline")
    assert schema.anchor_present("MAY 1", "due may   1")  # case + ws-insensitive
    assert not schema.anchor_present("may 2", "due may 1")
    assert schema.normalize_text("  A\n\tB  ") == "a b"
    assert schema.sha256_bytes(b"x").startswith("sha256:")
    assert schema.depth_bucket_for(1, 30) == "early"
    assert schema.depth_bucket_for(15, 30) == "middle"
    assert schema.depth_bucket_for(30, 30) == "late"


def test_fixture_vs_gold_content_never_contains_answer_key_headers(tmp_path) -> None:
    """Smoke: committed fixtures carry no answer-key vocabulary at the JSON
    level (anchors only live in gold/); gold never appears under fixtures/."""
    anchor_vocab = {"verbatim_anchor", "planted_units", "salient_units", "attribution_hazards"}
    for fixture in COMMITTED_FIXTURES:
        assert "gold" not in fixture.name
        text = fixture.read_text()
        for key in anchor_vocab:
            assert f'"{key}"' not in text, f"{fixture.name} leaks answer-key vocabulary {key}"


def test_verify_manifest_reports_non_object_document(tmp_path) -> None:
    """REVIEW-FIX (F1, code-review gate): a corrupted ``_manifest.json`` that
    parses to a NON-dict (e.g. ``[]`` from a botched merge) is REPORTED as
    malformed — never a crash. ``verify_manifest``'s docstring promises the
    gate reports, never raises."""
    from tests.eval.write_path import corpus as corpus_mod

    manifest_path = tmp_path / "_manifest.json"
    manifest_path.write_text("[]\n")
    verification = corpus_mod.verify_manifest(tmp_path)
    assert not verification["ok"]
    assert verification["malformed"], \
        "non-object manifest document must set the malformed field"
    assert "not an object" in verification["malformed"]


def test_validate_gold_reports_non_object_fixture() -> None:
    """REVIEW-FIX (F2, code-review gate): a gold↔fixture cross-check against a
    fixture that parses to a NON-dict (corrupted fixture file) reports issues
    — never crashes. The validators' list[str]-of-issues contract holds on
    garbage documents, not just garbage fields inside dicts."""
    from tests.eval.write_path import corpus as corpus_mod
    import tests.eval.write_path.schema as schema_mod

    gold = corpus_mod.load_gold(COMMITTED_SESSIONS[0])
    issues = schema_mod.validate_gold(gold, fixture=["not", "a", "fixture"])
    assert any("fixture" in i and "not an object" in i for i in issues), issues
    # A non-dict fixture must not crash the depth-bucket / anchor cross-checks
    # (they route through _safe_turn/_conversation_len — hardened).
    assert isinstance(issues, list)


def test_validate_gold_non_object_fixture_still_checks_gold() -> None:
    """REVIEW-FIX (P2-1, re-review): a non-object fixture downgrades ONLY the
    gold↔fixture CROSS-checks — the gold-only content checks (schema_version,
    unknown keys, planted/salient units) still run. A malformed fixture must
    never mask a malformed gold."""
    from tests.eval.write_path import corpus as corpus_mod
    import tests.eval.write_path.schema as schema_mod

    gold = corpus_mod.load_gold(COMMITTED_SESSIONS[0])
    gold["schema_version"] = 999  # gold is malformed regardless of fixture
    issues = schema_mod.validate_gold(gold, fixture=["not", "a", "fixture"])
    assert any("fixture" in i and "not an object" in i for i in issues), issues
    assert any("schema_version" in i for i in issues), \
        "gold-only checks must still run when the fixture is malformed (P2-1)"


def test_validate_baseline_rejects_partial_metric_vocabulary() -> None:
    """REVIEW-FIX (P2-2, re-review): validate_baseline — the validator that
    gates committed baseline files — must reject a published baseline whose
    metrics snapshot fewer than the full METRIC_VALUES vocabulary (a
    hand-edited partial baseline would silently shrink the CI-gate compare
    set). Mirrors the bless-path completeness rule."""
    from tests.eval.write_path import corpus as corpus_mod
    import tests.eval.write_path.schema as schema_mod

    full = corpus_mod.load_baseline()
    full["judge_pin"] = "judge-write-path-v1"
    full["justification"] = "published"
    full["metrics"] = {"salient_unit_survival_macro": 0.83}  # 5 of 6 missing
    issues = schema_mod.validate_baseline(full)
    assert any("missing graded dimensions" in i for i in issues), issues
    # The full-vocabulary published baseline validates clean (control).
    full["metrics"] = {
        "salient_unit_survival_macro": 0.83,
        "salient_unit_survival_strict": 0.78,
        "distractor_leakage_per_run": 1,
        "sessions_emitting": 1.0,
        "quote_fidelity": 0.84,
        "provenance_accuracy": 0.86,
    }
    assert schema_mod.validate_baseline(full) == []
