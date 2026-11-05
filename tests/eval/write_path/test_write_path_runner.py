"""W2-b runner orchestration contract (issue #2098) — hermetic, no DB.

Runner-level contract over ``runner.py`` WITHOUT a live graph: pre-flight
gates, store-file render → real-parser round-trip for every committed
harness, run failure paths (pre-flight failures never raise; runner errors
are surfaced with a §6.6 origin), receipt build/validate invariants, the
CLI bless flow against a temporary corpus copy, and the aggregate/compare/
bless loop semantics the CI gate rides on.

The DB-touching replay that used to live beside this file
(`test_write_path_benchmark.py`, the docker lane) was deleted with the SDK's
`capture_session` in #4282; this file is the pure orchestration layer
(test-design #2093 S4: no DB, no network, no LLM) and survives intact.
"""
from __future__ import annotations

import json
import shutil

import pytest

from tests.eval.write_path import corpus, runner, schema
from tests.eval.write_path.judge import JUDGE_PIN_MECHANICAL


def _tmp_corpus(tmp_path) -> object:
    """A byte-identical copy of the committed corpus in a temp root, with the
    baseline reset to first-run-pending — the live committed baseline is now
    PUBLISHED (W2-b #2098 published the first real number), and the runner CLI
    bless tests exercise the first-publish path, which requires a pending
    baseline in the temp root."""
    dst = tmp_path / "corpus"
    shutil.copytree(corpus.WRITE_PATH_DIR, dst, ignore=shutil.ignore_patterns(
        "runs", "__pycache__", "test_*.py"))
    baseline_path = dst / "baselines" / "main.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline.update({
        "judge_pin": None,
        "justification": None,
        "metrics": {},
        "history": [],
    })
    baseline_path.write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dst


# ── Store render + real parser round-trip (S1) ─────────────────────────────


@pytest.mark.parametrize(
    ("session_id", "harness"),
    [("wp01_quarry_debug", "codex"),
     ("wp03_ember_design", "pi"),
     ("wp05_retro_writeup", "claude-desktop")],
)
def test_parser_roundtrip_is_byte_identical(session_id, harness, tmp_path):
    fixture = corpus.load_fixture(session_id)
    conversation = fixture["conversation"]
    runner.parse_roundtrip(
        session_id, conversation, fixture["harness"], workdir=tmp_path
    )
    # The round-trip guard is `parse_roundtrip`'s RunError on any drift — it
    # never returns a drifted list, so a deep-equality/role/content assert
    # here would be dead (the drift case is covered by
    # test_parse_roundtrip_drift_raises). What this test CAN still fail on is
    # the fixture SHAPE: a turn growing a key the round-trip contract does not
    # cover would slip past parse_roundtrip undetected.
    assert {k for t in conversation for k in t} == {"role", "content"}


def test_parse_roundtrip_drift_raises(tmp_path):
    # #2174-lint F841: the old `conversation` fixture was unused — the drift
    # case builds its own `drifted` transcript below.
    # Malformed render for the codex parser: content is a string, not a part
    # array — the parser still flattens strings via _text_from_parts. Force a
    # drift by rendering a turn the parser will drop (empty content).
    drifted = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": ""}]
    with pytest.raises(runner.RunError):
        runner.parse_roundtrip("drift", drifted, "codex", workdir=tmp_path)


def test_unknown_harness_render_raises(tmp_path):
    from tortoise.session_import.parsers import parse_transcript

    with pytest.raises(ValueError):
        parse_transcript(tmp_path / "nope.jsonl", "not-a-harness")


# ── Pre-flight ─────────────────────────────────────────────────────────────


def test_preflight_ok_on_committed_corpus():
    pf = runner.preflight()
    assert pf["ok"] is True, pf["issues"]
    assert pf["fixtures_hash"].startswith("sha256:")
    # The committed baseline is PUBLISHED after W2-b's first run (#2098) —
    # preflight validates it clean (full vocabulary + pin + justification).
    assert pf["baseline"]["metrics"] != {}
    assert schema.validate_baseline(pf["baseline"]) == []


def test_preflight_catches_gold_only_edit(tmp_path):
    root = _tmp_corpus(tmp_path)
    gold_path = root / "gold" / "wp01_quarry_debug.gold.json"
    gold = json.loads(gold_path.read_text())
    gold["scenario"] = gold["scenario"] + " (tampered)"  # gold-only drift
    gold_path.write_text(json.dumps(gold))
    pf = runner.preflight(root)
    assert pf["ok"] is False
    assert any("fixtures_hash" in i or "manifest" in i for i in pf["issues"])


def test_preflight_rejects_gold_inside_fixture(tmp_path):
    root = _tmp_corpus(tmp_path)
    fixture_path = root / "fixtures" / "wp01_quarry_debug.json"
    fixture = json.loads(fixture_path.read_text())
    fixture["gold"] = {"leaked": "answer key"}  # the sealed-key boundary
    fixture_path.write_text(json.dumps(fixture))
    pf = runner.preflight(root)
    assert pf["ok"] is False
    assert any("gold" in i and "VALIDATION ERROR" in i for i in pf["issues"])


# ── Receipt invariants ─────────────────────────────────────────────────────


def _completed_report() -> dict:
    return {
        "run_id": "w2b-test-run",
        "date": "2026-09-01T00:00:00Z",
        "run_status": "completed",
        "verdict": "pass",
        "failure_origin": None,
        "commit": "abc123",
        "corpus_hash": "sha256:deadbeef",
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "resolved_config": dict(corpus.BASELINE_CONFIG),
        "cost_usd": 0.0,
        "metrics": {
            "salient_unit_survival_macro": 0.9,
            "salient_unit_survival_strict": 0.8,
            "distractor_leakage_per_run": 1,
            "sessions_emitting": 1.0,
            "quote_fidelity": 1.0,
            "provenance_accuracy": 1.0,
        },
        "session_results": [],
        "notes": [],
        "log": [],
    }


def test_receipt_validates_when_complete():
    receipt = runner.build_receipt(_completed_report(), justification=None)
    assert runner.validate_receipt(receipt) == []


def test_receipt_rejects_completed_run_without_judge_pin():
    report = _completed_report()
    report["judge_pin"] = None
    receipt = runner.build_receipt(report)
    issues = runner.validate_receipt(receipt)
    assert any("judge_pin" in i for i in issues)


def test_receipt_rejects_partial_metrics_on_completed_run():
    report = _completed_report()
    report["metrics"] = {"salient_unit_survival_macro": 0.9}
    receipt = runner.build_receipt(report)
    issues = runner.validate_receipt(receipt)
    assert any("missing metrics" in i for i in issues)


def test_failed_run_receipt_allows_null_pin_and_empty_metrics():
    receipt = runner.build_receipt(runner._failed_report(
        "w2b-x", "2026-09-01T00:00:00Z", "abc", "sha256:y",
        dict(corpus.BASELINE_CONFIG), origin="runner_error", detail="boom",
    ))
    assert runner.validate_receipt(receipt) == []


# ── CLI bless flow against a temporary corpus copy ─────────────────────────


def _published_receipt(tmp_path) -> tuple[object, object]:
    """Build a publishable completed-run receipt over the tmp corpus."""
    root = _tmp_corpus(tmp_path)
    baseline = corpus.load_baseline(root)  # pending state
    metrics = {
        "salient_unit_survival_macro": 0.55,
        "salient_unit_survival_strict": 0.31,
        "distractor_leakage_per_run": 0,
        "sessions_emitting": 1.0,
        "quote_fidelity": 1.0,
        "provenance_accuracy": 1.0,
    }
    run = {
        "date": "2026-09-01T00:00:00Z",
        "fixtures_hash": baseline["fixtures_hash"],
        "config": dict(corpus.BASELINE_CONFIG),
        "metrics": metrics,
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "failure_classes": ["triage misses on buried-signal transcripts"],
    }
    # #2174-lint F841: bless_baseline publishes (writes) the baseline — the
    # call matters, the unused return doesn't.
    schema.bless_baseline(baseline, run, justification="first published baseline (bad number, per protocol)")
    receipt = {
        "run_id": "w2b-first",
        "date": run["date"],
        "run_status": "completed",
        "verdict": schema.VERDICT_INCONCLUSIVE,  # first publish — nothing compared yet
        "failure_origin": None,
        "commit": "abc123",
        "corpus_hash": baseline["fixtures_hash"],
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "resolved_config": dict(corpus.BASELINE_CONFIG),
        "cost_usd": 0.0,
        "metrics": metrics,
        "session_results": [{
            "session_id": "wp01_quarry_debug", "emitted": True,
            "macro": {"survived": 9, "total": 16},
            "strict": {"survived": 5, "total": 16},
            "leaked": [], "quotes": {"grounded": 0, "total": 0},
            "provenance": {"provenanced": 44, "total": 44},
            "memory_points": 44, "operator_counts": {"IMPL": 40},
            "failed_units": [{"id": "u_01", "failure": "content_missing"}],
        }],
        "notes": [], "log": [],
    }
    return root, receipt


def test_cli_bless_publishes_baseline_with_justification(tmp_path, capsys):
    root, receipt = _published_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    exit_code = runner._main([
        "bless", "--receipt", str(receipt_path),
        "--justification", "first published baseline (bad number, per protocol)",
        "--write", "--root", str(root),
    ])
    assert exit_code == runner.EXIT_OK
    committed = corpus.load_baseline(root)
    assert committed["metrics"]["salient_unit_survival_macro"] == 0.55
    assert committed["judge_pin"] == JUDGE_PIN_MECHANICAL
    assert committed["justification"].startswith("first published baseline")
    assert schema.validate_baseline(committed) == []
    # fix-wave trail records the entry
    assert len(committed["history"]) == 1
    assert committed["history"][0].get("verdict") is None  # first publish


def test_cli_bless_requires_justification(tmp_path):
    root, receipt = _published_receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    exit_code = runner._main([
        "bless", "--receipt", str(receipt_path), "--justification", "  ",
        "--write", "--root", str(root),
    ])
    assert exit_code == runner.EXIT_RUNNER_ERROR
    assert corpus.load_baseline(root)["metrics"] == {}  # untouched


def test_cli_bless_rejects_regression_without_blessing(tmp_path):
    """A subsequent RUN whose metrics regress needs the comparison to say
    regression and blessing it records the verdict — but the *receipt* the
    runner produces for a regression run must be blessed knowingly (the
    justification covers it). Here we assert the guard rails: blessing a
    run whose metrics are WORSE than the committed targets is allowed ONLY
    with a justification (schema-level), and the CLI never blesses an
    inconclusive run against committed targets."""
    root = _tmp_corpus(tmp_path)
    # First: publish a good baseline on the tmp corpus via the API.
    pending = corpus.load_baseline(root)
    run1 = {
        "date": "2026-09-01T00:00:00Z",
        "fixtures_hash": pending["fixtures_hash"],
        "config": dict(corpus.BASELINE_CONFIG),
        "metrics": {"salient_unit_survival_macro": 0.8,
                    "salient_unit_survival_strict": 0.7,
                    "distractor_leakage_per_run": 0,
                    "sessions_emitting": 1.0,
                    "quote_fidelity": 1.0,
                    "provenance_accuracy": 1.0},
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "failure_classes": [],
    }
    blessed = schema.bless_baseline(
        pending, run1, justification="first published baseline"
    )
    (root / "baselines" / "main.json").write_text(json.dumps(blessed, indent=2))
    # Regression run: strict survival collapsed (provenance stripped).
    run2_metrics = {"salient_unit_survival_macro": 0.8,
                    "salient_unit_survival_strict": 0.0,
                    "distractor_leakage_per_run": 0,
                    "sessions_emitting": 1.0,
                    "quote_fidelity": 1.0,
                    "provenance_accuracy": 0.0}
    verdict = schema.compare_run(
        run2_metrics, blessed,
        resolved_config=dict(corpus.BASELINE_CONFIG),
        run_fixtures_hash=pending["fixtures_hash"],
    )
    assert verdict == schema.VERDICT_REGRESSION
    # Blessing the regression REQUIRES a justification (gbrain decision 4).
    with pytest.raises(ValueError):
        schema.bless_baseline(blessed, {**run1, "metrics": run2_metrics},
                              justification="")
    regressed = schema.bless_baseline(
        blessed, {**run1, "metrics": run2_metrics},
        justification="provenance strip regression — blessing the bad number per fix-wave protocol",
    )
    assert regressed["history"][-1]["verdict"] == schema.VERDICT_REGRESSION
    assert schema.validate_baseline(regressed) == []
    # CLI refuses to bless an inconclusive-vs-targets receipt.
    inconclusive_receipt = {
        **{"run_id": "w2b-first", "date": "2026-09-01T00:00:00Z",
           "run_status": "completed",
           "failure_origin": None, "commit": "abc123",
           "corpus_hash": pending["fixtures_hash"],
           "judge_pin": JUDGE_PIN_MECHANICAL,
           "resolved_config": dict(corpus.BASELINE_CONFIG),
           "cost_usd": 0.0, "session_results": [], "notes": [], "log": []},
        "verdict": schema.VERDICT_INCONCLUSIVE,
        "metrics": run2_metrics,
    }
    ipath = tmp_path / "inconclusive.json"
    ipath.write_text(json.dumps(inconclusive_receipt))
    exit_code = runner._main([
        "bless", "--receipt", str(ipath),
        "--justification", "fix-wave bless", "--write", "--root", str(root),
    ])
    assert exit_code == runner.EXIT_RUNNER_ERROR


# ── Runner failure surface (no DB) ─────────────────────────────────────────


def test_cli_corpus_bless_refreshes_published_baseline(tmp_path, capsys):
    """REVIEW-FIX (PR #2183 finding 2): --corpus-bless accepts an INTENTIONAL
    corpus regeneration against a PUBLISHED baseline — re-pins the new hash,
    records corpus_change in history, no compare. The ordinary bless path
    still rejects a drifted run."""
    root = _tmp_corpus(tmp_path)
    # Publish a baseline first (pending → published on this tmp corpus).
    baseline = corpus.load_baseline(root)
    run = {
        "date": "2026-09-01T00:00:00Z",
        "fixtures_hash": baseline["fixtures_hash"],
        "config": dict(corpus.BASELINE_CONFIG),
        "metrics": {
            "salient_unit_survival_macro": 0.55,
            "salient_unit_survival_strict": 0.31,
            "distractor_leakage_per_run": 0,
            "sessions_emitting": 1.0,
            "quote_fidelity": 1.0,
            "provenance_accuracy": 1.0,
        },
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "failure_classes": [],
    }
    blessed_first = schema.bless_baseline(baseline, run, justification="first baseline")
    # REVIEW-FIX (round-3 F5): actually PUBLISH the first baseline on disk
    # (write to the baseline file) so the CLI corpus-bless below runs against
    # a PUBLISHED baseline with committed targets — the branch this test's
    # name + docstring claim to cover. (Previously the bless result was
    # discarded, leaving main.json pending and silently testing the
    # first-publish path instead.)
    target = root / "baselines" / "main.json"
    target.write_text(json.dumps(blessed_first, indent=2) + "\n", encoding="utf-8")
    committed_check = corpus.load_baseline(root)
    assert committed_check.get("metrics"), \
        "precondition: baseline must be PUBLISHED (non-empty metrics) on disk"
    # A drifted (post-regeneration) run receipt on the PUBLISHED baseline.
    receipt_path = tmp_path / "drifted-receipt.json"
    receipt_path.write_text(json.dumps({
        "run_id": "w2b-after-regen", "date": "2026-09-20T00:00:00Z",
        "run_status": "completed", "verdict": schema.VERDICT_INCONCLUSIVE,
        "failure_origin": "hash_mismatch", "commit": "abc123",
        "corpus_hash": "sha256:regenerated-corpus", "judge_pin": JUDGE_PIN_MECHANICAL,
        "resolved_config": dict(corpus.BASELINE_CONFIG),
        "cost_usd": 0.0,
        "metrics": {
            "salient_unit_survival_macro": 0.7,
            "salient_unit_survival_strict": 0.6,
            "distractor_leakage_per_run": 1,
            "sessions_emitting": 1.0,
            "quote_fidelity": 1.0,
            "provenance_accuracy": 1.0,
        },
        "session_results": [], "notes": [], "log": [],
    }))
    # Ordinary bless rejects the drifted hash.
    exit_code = runner._main([
        "bless", "--receipt", str(receipt_path),
        "--justification", "drifted — should fail without corpus-bless",
        "--root", str(root),
    ])
    assert exit_code == runner.EXIT_RUNNER_ERROR
    # --corpus-bless accepts it and re-pins.
    exit_code = runner._main([
        "bless", "--receipt", str(receipt_path), "--corpus-bless",
        "--justification", "intentional corpus regeneration (fixture fix)",
        "--write", "--root", str(root),
    ])
    assert exit_code == runner.EXIT_OK
    committed = corpus.load_baseline(root)
    assert committed["fixtures_hash"] == "sha256:regenerated-corpus"
    assert committed["history"][-1].get("corpus_change") is True
    assert schema.validate_baseline(committed) == []


def test_operator_audit_m2_clause_is_posture_scoped():
    """#2552/#4807 honesty: the m2-echo-lane caveat ("no product relation
    extraction, so its edge score is not comparable with the llm lane's") must
    appear ONLY in an m2-lane report. On the llm lane a 0 is a behavioural
    signal; printing the structural excuse in that receipt frames the
    behavioural result as a non-result in the very artifact a reader consults
    (the pre-fix behaviour, reported on #2552).

    Pure posture-gate check — no bench run, no graph.
    """
    audit = {"planted": 4, "edge_correct": 0, "content_ok": 1,
             "operators_total": 10, "operators_provenanced": 10}

    llm_notes = runner.operator_audit_notes(audit, "llm")
    llm_text = "\n".join(llm_notes)
    assert "operator-edge audit (#2514): 0/4" in llm_text
    assert "operator persistence (#2552): 10/10" in llm_text
    assert "m2 echo lane has no product relation extraction" not in llm_text

    m2_text = "\n".join(runner.operator_audit_notes(audit, "m2"))
    assert "m2 echo lane has no product relation extraction" in m2_text
    # The persistence assertion rides BOTH lanes (write-path property).
    assert "operator persistence (#2552): 10/10" in m2_text

    # EXACT-STRING goldens. The m2 note's prose is DERIVED — no hardcoded `0`
    # and no "no relation extraction" claim (#4807) — and pinned
    # character-for-character: a substring assert cannot hold the wording (both
    # separator defects found while rebasing #4806 passed every substring
    # assert). The committed m2 receipt keeps the OLD text; no test reads the
    # receipts and no baseline carries `notes`, so the source change reaches no
    # hashed surface (#4807 verification).
    m2 = runner.operator_audit_notes(audit, "m2")
    llm = runner.operator_audit_notes(audit, "llm")
    assert m2[0] == (
        "operator-edge audit (#2514): 0/4 planted operator edges graded "
        "edge_correct (audit dimension only — not yet a gated metric); "
        "the m2 echo lane has no product relation extraction, so its "
        "edge score is not comparable with the llm lane's and is never a "
        "bar. #2552: endpoint anchors + mitigation reasons grade "
        "verbatim-first with the #2405-style paraphrase band — a "
        "correctly wired edge whose endpoint claim was distilled still "
        "grades edge_correct"
    )
    assert llm[0] == (
        "operator-edge audit (#2514): 0/4 planted operator edges graded "
        "edge_correct (audit dimension only — not yet a gated metric). "
        "#2552: endpoint anchors + mitigation reasons grade verbatim-first "
        "with the #2405-style paraphrase band — a correctly wired edge whose "
        "endpoint claim was distilled still grades edge_correct"
    )
    assert m2[1] == llm[1] == (
        "operator persistence (#2552): 10/10 reified operator Points entered "
        "the retrievable memory layer (eventId-stamped) — a lower numerator "
        "is the structural drop the layer-2 WIRE fix closed"
    )
    # Count and order are part of the contract: edge note first, then the
    # persistence note, and nothing else (a third note would go unnoticed).
    assert len(m2) == len(llm) == 2

    # A third posture takes the llm-shaped note (documented behaviour: only
    # "m2" earns the caveat, never "anything not llm").
    assert "m2 echo lane" not in "\n".join(
        runner.operator_audit_notes(audit, "futurelane"))

    # Empty denominator: no edge note, persistence still rides. A None audit
    # yields nothing.
    empty = runner.operator_audit_notes(
        {"planted": 0, "edge_correct": 0, "operators_total": 3,
         "operators_provenanced": 3}, "llm")
    assert not any("operator-edge audit" in n for n in empty)
    assert any("operator persistence (#2552)" in n for n in empty)
    assert runner.operator_audit_notes(None, "llm") == []


def test_operator_audit_m2_note_agrees_with_any_numerator():
    """#4807: the m2 note's prose is DERIVED from the interpolated
    ``{edge_correct}/{planted}`` numerator, so it must stay true for ANY value
    — including the 2/15 the committed receipt grades, and any future m2 lane
    beyond it. The pre-fix text hardcoded "so 0 is structural there" three
    words after the number, so 2/15 printed as a self-contradiction.

    Two independent properties, both pinned over the whole numerator range:
      * the printed numerator matches the audit it was built from, and
      * no constant-"0" structural clause and no "no relation extraction"
        mechanism claim survives at any numerator (the false half that a
        number-only fix would have left standing).
    """
    den = 15
    for num in (0, 1, 2, 7, 15):
        note = runner.operator_audit_notes(
            {"planted": den, "edge_correct": num, "content_ok": 1,
             "operators_total": 1, "operators_provenanced": 1}, "m2")[0]
        assert f"{num}/{den} planted operator edges" in note
        assert "0 is structural" not in note
        assert "so 0" not in note
        # The false mechanism cannot reappear: "has no relation extraction" is
        # NOT a substring of the corrected "has no product relation
        # extraction".
        assert "has no relation extraction" not in note
        assert "has no product relation extraction" in note


def test_cli_protocol_bless_repins_judge_bump(tmp_path):
    """REVIEW-FIX (round-3 G2): --protocol-bless is the operational re-pin
    path for a legitimate judge-protocol bump — the ordinary CLI bless
    rejects the pin change; --protocol-bless accepts it with justification
    and records protocol_change in history."""
    root = _tmp_corpus(tmp_path)
    # Publish a baseline first (llm posture, judge-write-path-v1).
    baseline = corpus.load_baseline(root, posture="llm")
    run = {
        "date": "2026-09-01T00:00:00Z",
        "fixtures_hash": baseline["fixtures_hash"],
        "config": dict(corpus.BASELINE_CONFIG),
        "metrics": {
            "salient_unit_survival_macro": 0.55,
            "salient_unit_survival_strict": 0.31,
            "distractor_leakage_per_run": 0,
            "sessions_emitting": 1.0,
            "quote_fidelity": 1.0,
            "provenance_accuracy": 1.0,
        },
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "failure_classes": [],
    }
    first = schema.bless_baseline(baseline, run, justification="first baseline")
    (root / "baselines" / "main.json").write_text(
        json.dumps(first, indent=2) + "\n", encoding="utf-8")
    # A receipt under the NEW judge pin (same corpus, same numbers).
    receipt_path = tmp_path / "protocol-receipt.json"
    receipt_path.write_text(json.dumps({
        "run_id": "w2b-protocol-v2", "date": "2026-09-25T00:00:00Z",
        "run_status": "completed", "verdict": schema.VERDICT_INCONCLUSIVE,
        "failure_origin": None, "commit": "abc123",
        "corpus_hash": baseline["fixtures_hash"], "judge_pin": "judge-write-path-v2",
        "resolved_config": dict(corpus.BASELINE_CONFIG),
        "cost_usd": 0.0, "metrics": run["metrics"],
        "session_results": [], "notes": [], "log": [],
    }))
    # Ordinary bless rejects the pin change (protocol change, not comparable).
    exit_code = runner._main([
        "bless", "--receipt", str(receipt_path),
        "--justification", "should fail without protocol-bless",
        "--root", str(root),
    ])
    assert exit_code == runner.EXIT_RUNNER_ERROR
    # --protocol-bless re-pins with justification.
    exit_code = runner._main([
        "bless", "--receipt", str(receipt_path), "--protocol-bless",
        "--justification", "judge v2 protocol (blind-salience prompt fix)",
        "--write", "--root", str(root),
    ])
    assert exit_code == runner.EXIT_OK
    committed = corpus.load_baseline(root, posture="llm")
    assert committed["judge_pin"] == "judge-write-path-v2"
    assert committed["history"][-1].get("protocol_change") is True
    assert schema.validate_baseline(committed) == []


# ── #8503: attributing an `edge_missing` verdict ───────────────────────────
#
# An `edge_missing` verdict (both endpoints present, no qualifying edge) has
# TWO mechanisms that need different fixes: the model never FORMED the edge
# (operator selection) or the seam DROPPED one it did emit (a write-path bug).
# The receipts carried no extraction warnings at all, so a reader could not
# tell them apart. The block below is the discriminator; these tests pin both
# directions, because a filter that matched everything would answer "some
# warnings" rather than "no drop".


def test_operator_drop_warnings_names_a_seam_drop():
    results = [{
        "session_id": "wp01_quarry_debug",
        "extraction_warnings": [
            "operator dropped: src/dst did not resolve to an emitted "
            "point/event ('X' → 'Y')",
            "value-normalizer: 5 point(s) carried an amount that was not typed",
        ],
    }]
    drops = runner.operator_drop_warnings(results)
    assert drops["total"] == 1
    assert drops["operator_drops"] == 1
    assert drops["supersede_refusals"] == 0
    assert list(drops["by_session"]) == ["wp01_quarry_debug"]
    assert "operator dropped" in drops["by_session"]["wp01_quarry_debug"][0]


def test_operator_drop_warnings_separates_the_replace_channel():
    """#8503: a supersede refusal is NOT an operator drop.

    The first cut of this classifier matched "is not expressible in the
    Layer-1 payload" and counted wp02's three ENTITY-lifecycle notices plus
    wp07's two supersede refusals as five operator drops — attributing
    selection misses to the operator pass, which had dropped nothing. The two
    classes answer different questions and are counted separately.
    """
    results = [{
        "session_id": "wp07_bluepeak_followup",
        "extraction_warnings": [
            "entity 'all-at-once rollout' lifecycle=superseded with no "
            "supersedes ref is not expressible in the Layer-1 payload",
            "entity 'per-service flags' supersedes='all-at-once rollout' does "
            "not resolve to an S3 search result — recorded without a graph id",
            "point supersedes='We ship the lease fix' matches no S3 prior — "
            "skipped (fail-open)",
        ],
    }]
    drops = runner.operator_drop_warnings(results)
    assert drops["operator_drops"] == 0
    assert drops["supersede_refusals"] == 3
    assert drops["total"] == 3


def test_recall_leg_success_is_not_counted_as_a_refusal():
    """#8503 code-review finding: the refusal markers key on TAILS, not the
    `point supersedes=` prefix.

    The recall leg's SUCCESS warning shares that prefix, so the first cut of
    `_SUPERSEDE_REFUSAL_MARKERS` counted the one resolution that worked as a
    replace-channel DROP — the exact false-attribution class the two-class
    split exists to prevent.
    """
    results = [{
        "session_id": "wp07_bluepeak_followup",
        "extraction_warnings": [
            "point-supersede recall band (#8503): ref 'We ship the lease fix' "
            "is the same claim as 'pt_wp06_decision' once frame words at its edges are disregarded — resolved "
            "to that prior instead of failing open",
            "point supersedes='We ship the lease fix' matches no S3 prior — "
            "skipped (fail-open)",
        ],
    }]
    drops = runner.operator_drop_warnings(results)
    assert drops["supersede_refusals"] == 1, drops
    assert drops["operator_drops"] == 0


def test_operator_drop_warnings_ignores_benign_extraction_warnings():
    """Zero is a MEASUREMENT (#8503): "the extractor logged no drop".

    The real receipt's sessions log value-span, slot-binding and search_keys
    warnings — none of them a drop. A marker set that counted those would
    attribute every ``edge_missing`` to the write path.
    """
    results = [{
        "session_id": "wp01_quarry_debug",
        "extraction_warnings": [
            "value-bearing point 'X' resolved verbatim in turn 8, but the "
            "quoted text carries no value",
            "point 'Maya owns the pre-deploy check': search_keys capped at 4",
            "MITIGATES target edge materialized (#2552 mint-before-wire)",
            "operator endpoint minted (#2552 mint-before-wire): IMPL dst",
            "NAND direction canonicalized (#909 — new-claim-attacks-existing)",
        ],
    }]
    assert runner.operator_drop_warnings(results) == {
        "total": 0, "operator_drops": 0, "supersede_refusals": 0,
        "by_session": {}}


def test_entity_name_collision_is_not_a_supersede_refusal():
    """#8503 round 6: the refusal set must not swallow a notice that refuses nothing.

    `entity '<x>' kind '<y>' is ambiguous across namespaces … created with the
    explicit kind` CREATES the entity, so counting it as a replace-channel
    refusal made a receipt assert a drop cause that did not exist. The markers
    key on the resolver's own ambiguous forms only; this pin is what keeps a
    future "broaden the marker" edit from restoring the false cause.
    """
    collision = (
        "entity 'lumen auth' kind 'core:service' is ambiguous across "
        "namespaces in the existing graph — created with the explicit kind "
        "(link-before-create cannot disambiguate)")
    collision_only = runner.operator_drop_warnings(
        [{"session_id": "s", "extraction_warnings": [collision]}])
    assert collision_only == {"total": 0, "operator_drops": 0,
                             "supersede_refusals": 0, "by_session": {}}
    # EVERY resolver ambiguity form counts — the point-supersede branch
    # ("N priors contain it") as well as the lifecycle branch ("direction
    # unclear"), or a receipt claims no refusal on a run that refused one.
    for form in (
        "entity 'x' lifecycle=superseded with supersedes='y' is ambiguous "
        "(direction unclear) — supersession record skipped (never-guess)",
        "point supersedes='r' is ambiguous (2 priors contain it) — skipped "
        "(never guess)",
        "point supersedes='r' is ambiguous (2 priors) — skipped (never guess)",
    ):
        refused = runner.operator_drop_warnings(
            [{"session_id": "s", "extraction_warnings": [form]}])
        assert refused["supersede_refusals"] == 1, form


def test_run_note_never_claims_zero_drops_when_drops_exist():
    """#8503 round 5: the note's operator sentence tracks the operator count.

    The `elif supersede_refusals` branch was reachable with `operator_drops > 0`
    and still said "logged no operator drop" — a false statement in a shipped
    receipt. Four combinations, all pinned, and every assertion is on a note
    that must EXIST (an empty string once satisfied the drops-only check, which
    is how the missing note survived a gate).
    """
    def notes_for(operator_drops: int, refusals: int,
                  posture: str = "llm") -> str:
        audit = {
            "planted": 15, "edge_correct": 5, "content_ok": 11,
            "operators_total": 10, "operators_provenanced": 10,
            "drop_warnings": {"total": operator_drops + refusals,
                              "operator_drops": operator_drops,
                              "supersede_refusals": refusals,
                              "by_session": {}},
        }
        return "\n".join(
            runner.operator_drop_notes(audit["drop_warnings"], posture))

    both_zero = notes_for(0, 0)
    assert "logged no operator drop and no supersede refusal" in both_zero
    # the blind-spot enumeration is part of the claim: two seams, named.
    # (#9906 closed the third this assertion used to see — a point-level
    # `supersedes` ref on a folding point is now carried to the survivor, so it
    # reaches the resolver and its refusals ARE counted; naming it as a blind
    # spot would now be the false claim.)
    assert "two seams" in both_zero
    assert "three seams" not in both_zero
    assert "commit layer" in both_zero and "snapshot failed to read" in both_zero
    assert "NOOP" in both_zero    # the CLOSED seam is named as closed
    # drops only: THE note must exist and name the drops (an empty string would
    # satisfy a bare "not in" assertion — the vacuous pin review round 6 caught)
    drops_only = notes_for(2, 0)
    assert drops_only.strip(), "a run with drops must carry a drop note"
    assert "logged no operator drop" not in drops_only
    assert "2 drop warning(s)" in drops_only
    # refusals only: the operator sentence is truthful
    assert "the operator pass logged no drop" in notes_for(0, 3)
    # BOTH non-zero: must name the drops, never claim zero
    both = notes_for(2, 3)
    assert "logged no operator drop" not in both
    assert "2 drop warning(s)" in both
    assert "3 refusal(s)" in both


# ── #8503 code-review round 7: the note must know WHICH LANE it is on ───────

def test_the_zero_drop_note_does_not_claim_a_clean_bill_on_the_m2_lane():
    """The `drop_warnings` count is zero BY CONSTRUCTION on the m2 lane.

    Every marker `operator_drop_warnings` classifies is emitted by
    `tortoise/extractor_v2.py`, and the `m2` posture routes capture to the M2
    extractor, which never runs the product operator pass. So the zero note's
    conclusion — "an edge_missing verdict in this receipt is not an
    EXTRACTOR-side drop" — was an inference that lane's measurement cannot
    make, printed beside `operator_audit_notes`' own m2 caveat saying the lane
    has no product relation extraction at all. Two notes, one receipt,
    disagreeing about what the zero means.
    """
    def notes_for(operator_drops: int, refusals: int, posture: str) -> str:
        drops = {"total": operator_drops + refusals,
                 "operator_drops": operator_drops,
                 "supersede_refusals": refusals, "by_session": {}}
        return "\n".join(runner.operator_drop_notes(drops, posture))

    m2_zero = notes_for(0, 0, "m2")
    assert "NOT MEASURED" in m2_zero, m2_zero
    assert "product operator pass" in m2_zero
    assert "not an EXTRACTOR-side drop" not in m2_zero, (
        "the m2 lane cannot support the inference: its operator pass never runs")
    assert "three seams" not in m2_zero, (
        "the llm-lane blind spots do not bound a lane that never ran the pass")
    assert "two seams" not in m2_zero, (
        "the llm-lane blind spots do not bound a lane that never ran the pass")
    # two-sided: the llm lane still makes the claim it can make, and the four
    # count combinations are unchanged there.
    llm_zero = notes_for(0, 0, "llm")
    assert "not an EXTRACTOR-side drop" in llm_zero
    assert "NOT MEASURED" not in llm_zero
    assert "two seams" in llm_zero


def test_recall_leg_success_warning_is_not_a_refusal():
    """#8503 code-review finding: the classifier and the warning TEXT agree.

    The refusal markers key on refusal tails, and the recall leg's success
    line must therefore name no refusal. This pin runs the leg and classifies
    the warning it actually emits — a regression that re-uses the
    `point supersedes=… matches no S3 prior` shape for a SUCCESS fails here,
    not in a prose assertion.
    """
    import tortoise.extractor_v2 as v2

    embed = {"entities": [], "events": [],
             "points": [{"content": "roll out per-service flags for the next "
                                    "rollout instead of one global kill flag",
                          "pointKind": "statement",
                          "supersedes": "lease fix ships behind the canary "
                                        "flag at"}],
             "operators": []}
    search = {"entities": [], "events": [],
              "points": [{"id": "pt_wp06", "kind": "statement",
                          "content": "The lease fix ships behind the canary "
                                     "flag"}]}
    r = v2.execute_embed(embed, search, session_id="s1")
    assert any(s["superseded"] == "pt_wp06" for s in r["supersessions"])
    success = [w for w in r["warnings"] if "recall band" in w]
    assert success, r["warnings"]
    drops = runner.operator_drop_warnings(
        [{"session_id": "s1", "extraction_warnings": success}])
    assert drops["total"] == 0, drops


def test_non_dict_operator_drop_is_a_marker():
    """#8503 round 4: a malformed operator entry IS a drop and is classified.

    It never reaches the operator pass, so the pass's own drop line cannot
    cover it; without this marker the receipt reports `operator_drops == 0` on
    a run where an operator was skipped.
    """
    drops = runner.operator_drop_warnings([{
        "session_id": "wp01_quarry_debug",
        "extraction_warnings": [
            'non-dict operator entry "IMPL: a -> b" skipped'],
    }])
    assert drops["operator_drops"] == 1, drops
    assert drops["supersede_refusals"] == 0


def test_retraction_refusals_are_counted():
    """#8503 round 7: a refused RETRACTION is an extractor-side drop too.

    Two of the four retraction refusal forms matched no marker, so a run whose
    retractions were all refused classified as `{total: 0}` and shipped the
    "no supersede refusal in any session" note — the false-zero class again.
    """
    for form in (
        "non-dict retraction ref 'pt_abc123' skipped",
        "empty retraction ref (no id, no content) — skipped",
    ):
        drops = runner.operator_drop_warnings(
            [{"session_id": "s", "extraction_warnings": [form]}])
        assert drops["supersede_refusals"] == 1, form
        assert drops["operator_drops"] == 0, form


def test_receipt_omits_drop_warnings_when_the_audit_carries_none():
    """A receipt must not assert "0 drops" for a run that measured none."""
    report = _completed_report()
    report["operator_audit"] = {
        "planted": 15, "edge_correct": 2, "content_ok": 7,
        "operators_total": 27, "operators_provenanced": 27, "results": {},
    }
    receipt = runner.build_receipt(report)
    assert "drop_warnings" not in receipt["operator_audit"]


def test_receipt_carries_extraction_warnings_and_drop_attribution():
    report = _completed_report()
    report["session_results"] = [{
        "session_id": "wp01_quarry_debug",
        "emitted": True,
        "macro": {"survived": 1, "total": 2},
        "strict": {"survived": 1, "total": 2},
        "leaked": {"leaked": 0, "total": 0},
        "quotes": {"grounded": 0, "total": 0},
        "provenance": {"accurate": 1, "total": 1},
        "memory_point_count": 3,
        "operator_counts": {"IMPL": 2},
        "extraction_warnings": ["operator dropped: src/dst did not resolve"],
        "unit_detail": {},
    }]
    report["operator_audit"] = {
        "planted": 15, "edge_correct": 2, "content_ok": 7,
        "operators_total": 27, "operators_provenanced": 27,
        "drop_warnings": runner.operator_drop_warnings(
            report["session_results"]),
        "results": {},
    }
    receipt = runner.build_receipt(report)
    assert receipt["session_results"][0]["extraction_warnings"] == [
        "operator dropped: src/dst did not resolve"]
    assert receipt["operator_audit"]["drop_warnings"]["total"] == 1
    # a receipt that carries them still validates
    assert runner.validate_receipt(receipt) == []
