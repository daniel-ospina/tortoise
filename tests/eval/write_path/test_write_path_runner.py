"""W2-b runner orchestration contract (issue #2098) — hermetic, no DB.

Runner-level contract over ``runner.py`` WITHOUT a live graph: pre-flight
gates, store-file render → real-parser round-trip for every committed
harness, run failure paths (pre-flight failures never raise; runner errors
are surfaced with a §6.6 origin), receipt build/validate invariants, the
CLI bless flow against a temporary corpus copy, and the aggregate/compare/
bless loop semantics the CI gate rides on.

The DB-touching replay itself lives in test_write_path_benchmark.py (the
docker lane); this file is the pure orchestration layer (test-design #2093
S4: no DB, no network, no LLM).
"""
from __future__ import annotations

import json
import shutil

import pytest

from tests.eval.write_path import corpus, generate_corpus, runner, schema
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


def test_run_fails_clean_on_preflight_break(tmp_path):
    root = _tmp_corpus(tmp_path)
    fixture_path = root / "fixtures" / "wp01_quarry_debug.json"
    fixture = json.loads(fixture_path.read_text())
    fixture["gold"] = {"leaked": "answer key"}
    fixture_path.write_text(json.dumps(fixture))
    report = runner.run_benchmark(root=root, session_ids=["wp01_quarry_debug"])
    assert report["run_status"] == "failed"
    # REVIEW-FIX (finding 6): gold-key-in-fixture breaks the manifest/hash
    # pre-flight → classified hash_mismatch (the audit-grade origin), not
    # a generic runner_error.
    assert report["failure_origin"] == "hash_mismatch"
    assert report["verdict"] == schema.VERDICT_INCONCLUSIVE
    assert report["metrics"] == {}


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


def test_runner_error_when_session_unknown(tmp_path):
    report = runner.run_benchmark(root=corpus.WRITE_PATH_DIR, session_ids=["nope_1"])
    assert report["run_status"] == "failed"
    assert report["failure_origin"] == "runner_error"
    assert "unknown sessions" in report["log"][-1]


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


def test_run_rejects_config_posture_contradicting_env(tmp_path, monkeypatch):
    """REVIEW-FIX (round-3 F3): a caller-supplied config may NOT relabel the
    run's extractor posture against the env lane selector — an llm-labeled
    config under m2 env (or vice versa) would compare against the wrong
    lane's baseline and/or dodge the llm standing leakage bar."""
    root = _tmp_corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # config says llm, env says m2 — contradiction is rejected up front.
    with pytest.raises(ValueError, match="contradicts the env lane selector"):
        runner.run_benchmark(
            root=root, config={"extractor_posture": "llm"})
    # env-aligned config posture is fine (env still owns the final label).
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "")  # llm lane
    report = runner.run_benchmark(root=root)  # no env m2 → llm default
    assert report["run_status"] == "completed"
    assert report["resolved_config"]["extractor_posture"] == "llm"
    # #2552 (end-to-end falsifier, free — this llm run already happens): the
    # llm lane's receipt must NOT carry the m2 structural excuse. On the
    # pre-fix code this assertion fails, so it gates the fix at zero added
    # bench cost.
    assert "m2 echo lane has no relation extraction" not in "\n".join(
        report.get("notes", []))


def test_run_notes_vacuous_quote_fidelity_and_untracked_cost(tmp_path, monkeypatch):
    """REVIEW-FIX (F2/F3 honesty): a corpus whose gold never quotes yields a
    VACUOUS quote_fidelity 1.0 — the report notes it + carries the numeric
    quote_spans_total (never read as a real fidelity bar); a seam that
    reports no llm_cost_usd fires the explicit cost-not-tracked note."""
    root = _tmp_corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    report = runner.run_benchmark(root=root)
    assert report["run_status"] == "completed"
    assert report["quote_spans_total"] == 0
    notes = "\n".join(report.get("notes", []))
    assert "VACUOUS" in notes and "quote_spans_total=0" in notes
    assert "cost not tracked" in notes
    assert report["cost_usd"] == 0.0


def test_operator_audit_m2_clause_is_posture_scoped():
    """#2552 honesty: the m2-echo-lane caveat ("no relation extraction, so
    0 is structural there") must appear ONLY in an m2-lane report. On the llm
    lane a 0 is a behavioural signal; printing the structural excuse in that
    receipt frames the behavioural result as a non-result in the very
    artifact a reader consults (the pre-fix behaviour, reported on #2552).

    Pure posture-gate check — no bench run, no graph.
    """
    audit = {"planted": 4, "edge_correct": 0, "content_ok": 1,
             "operators_total": 10, "operators_provenanced": 10}

    llm_notes = runner.operator_audit_notes(audit, "llm")
    llm_text = "\n".join(llm_notes)
    assert "operator-edge audit (#2514): 0/4" in llm_text
    assert "operator persistence (#2552): 10/10" in llm_text
    assert "m2 echo lane has no relation extraction" not in llm_text

    m2_text = "\n".join(runner.operator_audit_notes(audit, "m2"))
    assert "m2 echo lane has no relation extraction" in m2_text
    # The persistence assertion rides BOTH lanes (write-path property).
    assert "operator persistence (#2552): 10/10" in m2_text

    # EXACT-STRING goldens. The m2 note is byte-identical to the pre-refactor
    # wording at the same call site — the wording the committed m2 receipt's
    # note is built from (its numbers differ, `2/15` vs `0/4` here; the wording
    # does not). That is the property that makes this refactor safe on the
    # blessed lane, and a substring assert cannot hold it (both separator
    # defects found while rebasing passed every substring assert).
    m2 = runner.operator_audit_notes(audit, "m2")
    llm = runner.operator_audit_notes(audit, "llm")
    assert m2[0] == (
        "operator-edge audit (#2514): 0/4 planted operator edges graded "
        "edge_correct (audit dimension only — not yet a gated metric); "
        "the m2 echo lane has no relation extraction, so 0 is structural "
        "there, never a bar. #2552: endpoint anchors + mitigation reasons "
        "grade verbatim-first with the #2405-style paraphrase band — a "
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


def test_run_origin_judge_pin_mismatch(tmp_path, monkeypatch):
    """REVIEW-FIX (round-3 N3): a judge-pin-drifted inconclusive run must be
    distinguishable from a benign first-run-pending inconclusive - the
    receipt origin is judge_pin_mismatch, not None."""
    root = _tmp_corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # Reset m2.json to first-run-pending (the copied corpus carries the
    # PUBLISHED m2 baseline - this test needs a fresh lane to bless).
    pending = corpus.first_run_pending_baseline(root, posture="m2")
    (root / "baselines" / "m2.json").write_text(
        json.dumps(pending, indent=2) + "\n", encoding="utf-8")
    # Publish an m2 baseline under the mechanical pin (run config must carry
    # the m2 posture to match the pending baseline's config snapshot).
    baseline = corpus.load_baseline(root, posture="m2")
    run_config = dict(corpus.BASELINE_CONFIG)
    run_config["extractor_posture"] = "m2"
    run = {
        "date": "2026-09-01T00:00:00Z",
        "fixtures_hash": baseline["fixtures_hash"],
        "config": run_config,
        "metrics": {
            "salient_unit_survival_macro": 0.5,
            "salient_unit_survival_strict": 0.5,
            "distractor_leakage_per_run": 1,
            "sessions_emitting": 1.0,
            "quote_fidelity": 1.0,
            "provenance_accuracy": 1.0,
        },
        "judge_pin": JUDGE_PIN_MECHANICAL,
        "failure_classes": [],
    }
    first = schema.bless_baseline(baseline, run, justification="first baseline")
    (root / "baselines" / "m2.json").write_text(
        json.dumps(first, indent=2) + "\n", encoding="utf-8")
    # Force the run under a DIFFERENT judge pin (protocol drift).
    monkeypatch.setattr(runner.judge, "JUDGE_PIN_MECHANICAL",
                        "judge-write-path-v2-different")
    report = runner.run_benchmark(root=root)
    assert report["run_status"] == "completed"
    assert report["verdict"] == schema.VERDICT_INCONCLUSIVE
    assert report["failure_origin"] == "judge_pin_mismatch"


def test_run_carries_operator_edge_audit_dimension(tmp_path, monkeypatch):
    """#2514: every completed run carries the planted-operator (layer-2)
    audit — the corpus-wide mechanical grade of whether the extractor wired
    the RIGHT operator edge between the anchored claims. On the deterministic
    m2 echo lane the operator EDGES do not match the planted semantics (the
    M2 MockModel relation stage is a cue-word heuristic, not the product
    extractor) — only a few edges match, endpoint-anchor pairs largely
    content-present — recorded as an additive audit dimension + note +
    receipt field, never a gated metric.

    #2552 (layer-2 WIRE): the m2 lane DOES commit operators; the structural
    fix makes them retrievable — every committed operator node carries the
    sessionCaptured eventId and enters the eventId-keyed memory layer
    (``operators_provenanced == operators_total``). The pre-fix signature was
    ``operators_total == 0`` on the retrievable surface with a silently empty
    ``operator_counts``.

    #2552 (measurement power): the gold grew 4 -> 15 planted edges across all
    seven sessions, so this asserts the count rather than a magic 4.
    """
    root = _tmp_corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    report = runner.run_benchmark(root=root)
    assert report["run_status"] == "completed", report.get("log")
    audit = report["operator_audit"]
    assert audit is not None
    # Pinned to the corpus FLOOR (a LOWER BOUND, so `>=`) AND tied to the
    # gold-derived ACTUAL count. Two different jobs, and neither is a literal,
    # so a grown corpus reddens nothing:
    #   * `>=` catches a total that drops BELOW the floor. The equality cannot
    #     (both sides read the same gold content, so they shrink together).
    #   * `== corpus.planted_operator_count()` catches an AUDIT that stops
    #     covering the corpus — the grader counts only the sessions the runner
    #     selected and collapses duplicate ids, while this count iterates the
    #     corpus itself. The floor cannot catch that.
    # NEITHER catches a within-floor shrink of the committed gold; that is
    # ``validate_committed``'s per-kind-floor job.
    assert audit["planted"] >= generate_corpus.MIN_PLANTED_OPERATOR_EDGES
    assert audit["planted"] == corpus.planted_operator_count()
    assert audit["edge_correct"] < audit["planted"]  # m2 cue-word relations
    assert 1 <= audit["content_ok"] <= audit["planted"]
    # #2552: the committed operator topology entered the retrievable layer.
    assert audit["operators_total"] > 0
    assert audit["operators_provenanced"] == audit["operators_total"]
    # Pin the receipt's own posture LABEL as well as the note text: the
    # call-site half of the posture gate rests on this run genuinely being m2.
    assert report["resolved_config"]["extractor_posture"] == "m2"
    notes = "\n".join(report.get("notes", []))
    assert "operator-edge audit (#2514)" in notes
    assert "operator persistence (#2552)" in notes
    # #2552: this is an m2 run, so its receipt MUST carry the m2 caveat. This is
    # the call-site half of the posture gate: without it, a call site passing
    # the wrong posture leaves every other test green while the blessed m2
    # receipt silently loses its caveat — the property this assertion protects.
    assert "m2 echo lane has no relation extraction" in notes
    # Per-session detail rides the owning session's result (the cross-session
    # SUPERSEDE is owned by wp07; its to-anchor lives in wp06's memory layer).
    owned_by = {
        r["session_id"]: r.get("planted_operators", []) for r in report["session_results"]
    }
    # Locate the cross-session SUPERSEDE by KIND, never by a positional or
    # per-session literal: `len(...) == 1` / `== 3` / `[2]` were hardcoded
    # operator counts that the #2552 gold growth only happened to leave valid
    # (it added edges to wp01-wp05) — the same hardcoded-denominator class the
    # rest of this file no longer contains.
    assert sum(len(v) for v in owned_by.values()) == \
        corpus.planted_operator_count()
    supersedes = [
        op for ops in owned_by.values() for op in ops
        if op.get("expected_kind") == "SUPERSEDE"
    ]
    # Lower-bounded by the corpus's OWN declared SUPERSEDE floor, never a magic
    # `1` — a second encoding of ``MIN_PLANTED_OPERATOR_KINDS["SUPERSEDE"]``
    # would keep accepting a corpus that violates the declared floor.
    assert len(supersedes) >= \
        generate_corpus.MIN_PLANTED_OPERATOR_KINDS["SUPERSEDE"]
    # Scope the TARGET assertion to the CROSS-SESSION SUPERSEDE. A second
    # planted SUPERSEDE is a legitimate measurement-power extension (see
    # ``MIN_PLANTED_OPERATOR_KINDS``), and one whose ``to`` is its own session
    # — the schema's default — carries ``to_session == owner_session``, so
    # "every SUPERSEDE targets wp06" would be the same hardcoded-corpus-shape
    # defect the rest of this commit removes.
    cross_session_supersedes = [
        op for op in supersedes
        if op.get("from_session") != op.get("to_session")
    ]
    assert cross_session_supersedes, supersedes
    # …and assert the PROPERTY that makes a cross-session CORRECTS possible at
    # all: the superseded claim must already be in the graph, so the TARGET's
    # session is captured BEFORE the owning one. Never pin the target to a
    # session NAME — a second genuine cross-session SUPERSEDE (wp03 -> wp01,
    # say) is a legitimate extension and a name pin would redden this lane for
    # no regression; the ordering property holds for every such edge.
    session_order = {
        r["session_id"]: i for i, r in enumerate(report["session_results"])
    }
    assert all(
        session_order[op["to_session"]] < session_order[op["from_session"]]
        for op in cross_session_supersedes
    ), cross_session_supersedes
    # The cross-session SUPERSEDE's detail needs no owner-name pin here, and must
    # not get one: ``runner.run_benchmark`` buckets each detail by its OWN
    # ``owner_session``, so a bucket assertion would be a TAUTOLOGY (a
    # cannot-fail guard, the #4261/#4222 family) rather than a check, while a
    # session-name pin would redden for no regression the moment a cross-session
    # SUPERSEDE is planted in another session. The exactly-once sum above already
    # proves this edge — like every other planted edge — landed in exactly one
    # bucket; its capture-order precondition is asserted just above.
    # The receipt must CARRY the audit block (it is the publish artifact).
    # ``build_receipt`` REBUILDS an explicit projection rather than copying the
    # report's block, so this equality is a real cross-object check — a
    # projection that drops or substitutes the key fails here. Pinned to the
    # gold-derived count (an independent source), never to the report's own
    # field and never to a literal.
    receipt = runner.build_receipt(report)
    assert runner.validate_receipt(receipt) == []
    assert "operator_audit" in receipt
    assert receipt["operator_audit"]["planted"] == corpus.planted_operator_count()
    assert (receipt["operator_audit"]["edge_correct"]
            < receipt["operator_audit"]["planted"])
    assert (receipt["operator_audit"]["operators_provenanced"]
            == receipt["operator_audit"]["operators_total"] > 0)
