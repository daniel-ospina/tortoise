"""#9080 capstone — the verdict gate and the run's refusal contract.

The issue's acceptance is two commands: ``probes/capstone_run.py`` (a real,
privileged run) and ``tools/capstone_verdict_check.py`` (the mechanical closing
rule). The CHECK is pure, so it is pinned here in full. The RUN is tested at
the only places a test can reach it without a provider key and a live graph:
its refusal chain (which is the half that ships in this lane — see
``docs/evidence/7949-capstone/README.md``) and the pure step/census observers
it derives every row from.

Why this matters beyond the two files. The epic's failure mode is *inference*:
a row marked green off a closed child, with no artifact behind it. Every
assertion below is one of the shapes that failure takes — a missing row, a
green row on a file that does not exist, a denominator inherited from a
different corpus, a green row whose evidence predates the thresholds it claims
to validate, a red row with no named cause. Each one has a stable finding code
(V1–V9) and a test that reds if the gate stops catching it.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import capstone_verdict_check as cvc  # noqa: E402

LOG = _REPO / "docs/evidence/7949-capstone/capstone.log"
PROBE = _REPO / "docs/evidence/7949-capstone/probes/capstone_run.py"


def _load_probe():
    """Load the probe by path — the evidence dir is hyphenated (no import)."""
    spec = _ilu.spec_from_file_location("capstone_run_7949", str(PROBE))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(_REPO), capture_output=True,
                          text=True, check=True).stdout.strip()


HEAD = _git("rev-parse", "HEAD")
PARENT = _git("rev-parse", "HEAD~1")


def _record(**over) -> dict:
    """A structurally CLEAN record; each test mutates exactly one rule."""
    row = {
        "criterion": "E1", "verdict": "UNPREREGISTERED",
        "evidence_kind": "refusal",
        "evidence": "docs/evidence/7949-capstone/capstone.log",
        "sha": HEAD, "numerator": None, "denominator": None,
        "corpus_sha256": None, "sibling": None, "blocked_by": "#9075",
        "note": "no pre-registration",
    }
    rows = [dict(row, criterion=f"E{i}") for i in range(1, 6)]
    rows[0]["blocked_by"] = "#9075"
    rec = {
        "schema": cvc.SCHEMA, "issue": 9080, "epic": 7949, "head": HEAD,
        "prereg": {"sha": HEAD, "status": "ABSENT",
                   "artifact": "docs/evidence/7949-capstone/preregistration.json"},
        "corpus": {"path": "corpus.json", "sha256": None, "turns": None},
        "run": {"status": "REFUSED", "reason": "no pre-registration",
                "steps_observed": [], "steps_required": list(cvc.STEPS)},
        "rows": rows,
    }
    rec.update(over)
    return rec


def _codes(record: dict, *, require_green: bool = False) -> list[str]:
    findings = cvc.check(record, log_path=LOG, repo=_REPO,
                         require_green=require_green)
    return [code for code, _w, _m in findings.items]


# ── the shipped evidence ──────────────────────────────────────────────────

def test_shipped_capstone_log_is_structurally_clean():
    """The committed log parses and passes every structural/honesty rule.

    This is the pin on the artifact: if the shipped table is regenerated with a
    row whose evidence does not exist, or without a blocker, THIS reds — the
    evidence bundle cannot quietly rot the way §10's prose did.
    """
    record = cvc.extract_block(LOG.read_text())
    findings = cvc.check(record, log_path=LOG, repo=_REPO, require_green=False)
    assert not findings, findings.render()
    assert [r["criterion"] for r in record["rows"]] == list(cvc.CRITERIA)


def test_shipped_log_is_not_closeable_and_says_so():
    """--require-green (the EPIC's rule) must fail: 0/5 rows are green.

    The lane that ships this bundle must not be able to read "the check passed"
    as "the epic can close". Two distinct questions, two distinct exits.
    """
    record = cvc.extract_block(LOG.read_text())
    codes = _codes(record, require_green=True)
    assert codes == ["V9"], codes
    assert {r["verdict"] for r in record["rows"]}.isdisjoint({"GREEN"})


def test_shipped_log_names_a_blocker_on_every_row():
    """Every non-green row carries its cause — the issue's explicit demand."""
    record = cvc.extract_block(LOG.read_text())
    for row in record["rows"]:
        assert row["blocked_by"].startswith("#"), row
    owners = {r["criterion"]: r["blocked_by"] for r in record["rows"]}
    # #5199 (E3 anchoring), #9077 (E4 real path), #7947 (E5 falsifier) are the
    # per-criterion lanes; #9075 gates E1/E2's thresholds.
    assert owners == {"E1": "#9075", "E2": "#9075", "E3": "#5199",
                      "E4": "#9077", "E5": "#7947"}, owners


# ── V2: the five-row contract ────────────────────────────────────────────

def test_missing_row_is_a_finding():
    rec = _record()
    rec["rows"] = rec["rows"][:4]
    assert "V2" in _codes(rec)


def test_duplicate_row_is_a_finding():
    rec = _record()
    rec["rows"][1]["criterion"] = "E1"
    assert "V2" in _codes(rec)


def test_unknown_criterion_is_a_finding():
    rec = _record()
    rec["rows"][4]["criterion"] = "E6"
    assert "V2" in _codes(rec)


# ── V4/V5: green means measured, with a file ─────────────────────────────

def _green(rec: dict) -> dict:
    row = rec["rows"][0]
    row.update(verdict="GREEN", evidence_kind="measurement",
               evidence="docs/evidence/7949-capstone/capstone.log",
               numerator=31, denominator=50, blocked_by=None,
               corpus_sha256="a" * 64)
    rec["corpus"] = {"path": "corpus.json", "sha256": "a" * 64, "turns": 50}
    rec["run"] = {"status": "COMPLETED", "reason": "eight steps",
                  "steps_observed": list(cvc.STEPS),
                  "steps_required": list(cvc.STEPS)}
    return rec


def test_green_row_with_missing_evidence_file_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["evidence"] = "docs/evidence/7949-capstone/never-written.log"
    assert "V4" in _codes(rec)


def test_row_without_an_evidence_path_is_a_finding():
    rec = _record()
    rec["rows"][0]["evidence"] = ""
    assert "V4" in _codes(rec)


def test_evidence_path_escaping_the_repo_is_a_finding():
    rec = _record()
    rec["rows"][0]["evidence"] = "../../etc/passwd"
    assert "V4" in _codes(rec)


def test_green_row_on_a_refusal_record_is_a_finding():
    """A refusal cannot be a measurement wearing a green verdict."""
    rec = _record()
    _green(rec)
    rec["rows"][0]["evidence_kind"] = "refusal"
    assert "V5" in _codes(rec)


def test_green_with_empty_denominator_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["denominator"] = 0
    assert "V5" in _codes(rec)


def test_green_row_naming_a_blocker_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["blocked_by"] = "#9076"
    assert "V5" in _codes(rec)


def test_green_without_all_eight_steps_observed_is_a_finding():
    """The epic measures the pipeline end to end — a step probe is not a row."""
    rec = _record()
    _green(rec)
    rec["run"]["steps_observed"] = ["S0", "S1", "S2.1", "S3", "S5", "S6"]
    assert "V5" in _codes(rec)


def test_green_row_whose_evidence_predates_the_preregistration_is_a_finding():
    """`0 rows green where the evidence artifact predates the pre-registration`."""
    rec = _record()
    _green(rec)
    rec["prereg"]["sha"] = HEAD
    rec["rows"][0]["sha"] = PARENT          # measured BEFORE the thresholds froze
    assert "V5" in _codes(rec)


def test_green_row_without_a_frozen_preregistration_is_a_finding():
    rec = _record()
    _green(rec)
    rec["prereg"]["sha"] = None
    assert "V5" in _codes(rec)


def test_green_row_with_a_sha_that_does_not_resolve_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["sha"] = "0" * 40
    assert "V5" in _codes(rec)


def test_green_row_with_an_unparseable_sha_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["sha"] = "not-a-sha"
    assert "V3" in _codes(rec)


# ── V6: a red row names its cause ────────────────────────────────────────

def test_non_green_row_without_a_blocker_is_a_finding():
    rec = _record()
    rec["rows"][2]["blocked_by"] = None
    assert "V6" in _codes(rec)


def test_non_green_row_with_a_non_issue_blocker_is_a_finding():
    rec = _record()
    rec["rows"][2]["blocked_by"] = "not landed yet"
    assert "V6" in _codes(rec)


def test_refusal_row_must_cite_the_log_it_is_recorded_in():
    rec = _record()
    rec["rows"][2]["evidence"] = "docs/evidence/7949-capstone/README.md"
    assert "V6" in _codes(rec)


# ── V7/V8: denominators carried, one corpus ──────────────────────────────

def test_measured_row_without_a_corpus_sha_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["corpus_sha256"] = None
    assert "V7" in _codes(rec)


def test_measured_row_on_a_different_corpus_is_a_finding():
    rec = _record()
    _green(rec)
    rec["rows"][0]["corpus_sha256"] = "b" * 64
    assert "V7" in _codes(rec)


def test_sibling_denominator_disagreement_is_a_finding():
    """E1 and E2 share one corpus; a restated denominator can drift."""
    rec = _record()
    _green(rec)
    rec["rows"][1].update(verdict="GREEN", evidence_kind="measurement",
                          evidence=rec["rows"][0]["evidence"],
                          numerator=30, denominator=50, blocked_by=None,
                          corpus_sha256="a" * 64, sibling="E1")
    rec["rows"][1]["numerator"] = 30
    assert "V8" in _codes(rec)


def test_sibling_names_an_absent_row_is_a_finding():
    rec = _record()
    rec["rows"][0]["sibling"] = "E9"
    assert "V8" in _codes(rec)


# ── the extract block ────────────────────────────────────────────────────

def test_missing_markers_is_a_usage_error(tmp_path=None):
    import pytest
    with pytest.raises(ValueError):
        cvc.extract_block("no table here")


def test_reversed_markers_is_a_usage_error():
    import pytest
    with pytest.raises(ValueError):
        cvc.extract_block(f"{cvc.END}\n{{}}\n{cvc.BEGIN}")


# ── the RUN's observers — every row is derived from these ────────────────

def _extract_result(**over) -> dict:
    result = {
        "stats": {"edus": 12, "chunks": 1,
                  "llm": {"by_stage": {"s1": {"calls": 1},
                                       "s2": {"calls": 1}}},
                  "s4_merge": {"s2_items": 15, "kept_from_s2": 15,
                               "merged_items": 15, "s4_items": 2,
                               "corrected_by_s4": 0,
                               "verbatim_reemissions": 0, "added_by_s4": 0}},
        "story_arc": "A narrative.",
        "embed_list": {"entities": [1], "events": [1], "points": [1],
                       "operators": [1]},
        "vet": {"enabled": True, "s2": {"kept": 3}},
        "classify_later": {"enabled": True, "s2": {"assigned": 3}, "union": {}},
        "search": {"found": []},
        "payload": {"points": []},
    }
    for k, v in over.items():
        if v is None:
            result.pop(k, None)
        else:
            result[k] = v
    return result


def test_all_eight_steps_observed_on_a_full_pass():
    seen = probe.observe_steps(_extract_result())
    assert set(seen) == set(cvc.STEPS), sorted(set(cvc.STEPS) - set(seen))
    assert all(isinstance(v, str) and v for v in seen.values())


def test_a_vet_disabled_pass_does_not_observe_s2_2():
    """The flag-off arm runs no S2.2 — so its table cannot claim S2.2."""
    seen = probe.observe_steps(_extract_result(vet={"enabled": False, "s2": {}}))
    assert "S2.2" not in seen


def test_a_classify_disabled_pass_does_not_observe_s2_3():
    seen = probe.observe_steps(_extract_result(
        classify_later={"enabled": False, "s2": {}, "union": {}}))
    assert "S2.3" not in seen


def test_a_pass_without_a_payload_does_not_observe_s6():
    seen = probe.observe_steps(_extract_result(payload=None))
    assert "S6" not in seen


def test_e4_receipt_flags_the_four_edge_denominator():
    """E4's row fails when the denominator is 4 — the historical defect."""
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 4, "kept_from_s2": 4}})
    assert receipt["denominator_is_the_4_edge_defect"] is True
    ok = probe.e4_receipt({"s4_merge": {"s2_items": 15, "kept_from_s2": 14}})
    assert ok["denominator_is_the_4_edge_defect"] is False
    assert (ok["numerator"], ok["denominator"]) == (14, 15)


def test_e3_census_counts_one_unanchored_object_as_a_failure():
    rows = [
        {"label": "Point", "id": "p1", "sourceVersion": "abc"},
        {"label": "Point", "id": "p2", "sourceVersion": "abc"},
        {"label": "Event", "id": "e1", "sourceVersion": "abc"},
        {"label": "Object", "id": "o1", "sourceVersion": None},
    ]
    census = probe.e3_census_from_rows(rows)
    assert census["denominator"] == 4
    assert census["numerator"] == 3
    assert census["unanchored_total"] == 1
    assert census["unanchored"] == [{"label": "Object", "id": "o1"}]
    assert census["by_label"] == {"Point": 2, "Event": 1, "Object": 1}


def test_e3_census_on_an_empty_graph_is_zero_not_green():
    census = probe.e3_census_from_rows([])
    assert (census["numerator"], census["denominator"]) == (0, 0)


def test_refused_rows_are_never_green_and_always_name_a_bug():
    rows = probe.refused_rows("no pre-registration", prereg_ok=False)
    assert [r["criterion"] for r in rows] == list(cvc.CRITERIA)
    assert {r["verdict"] for r in rows} == {"UNPREREGISTERED", "BLOCKED"}
    assert all(r["blocked_by"].startswith("#") for r in rows)
    assert all(r["evidence_kind"] == "refusal" for r in rows)


def test_after_the_preregistration_lands_the_rows_are_blocked_not_unpreregistered():
    """Distinct causes stay distinct: #9075 fixed does not fix E3/E4/E5."""
    rows = probe.refused_rows("no backend", prereg_ok=True)
    assert {r["verdict"] for r in rows} == {"BLOCKED"}


# ── the refusal chain, end to end ────────────────────────────────────────

def test_run_refuses_without_a_preregistration_and_emits_a_clean_table(tmp_path):
    """G0 refuses at HEAD: no commit carries the pre-registration artifact.

    This is the SHIPPED outcome (README: 5/5 rows not green). The assertion is
    not "it fails" — it is "it fails AND the table it leaves behind is one the
    checker accepts": a refusal that produced an unparseable log would let the
    next lane mistake silence for a verdict.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "capstone.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert record["run"]["status"] == "REFUSED"
    assert record["run"]["steps_observed"] == []
    findings = cvc.check(record, log_path=out, repo=_REPO,
                         require_green=False)
    assert not findings, findings.render()
    assert "V9" in _codes(record, require_green=True)


def test_read_preregistration_refuses_an_unknown_sha():
    record, why = probe.read_preregistration(
        "0" * 40, Path("docs/evidence/7949-capstone/preregistration.json"))
    assert record is None
    assert "does not resolve" in why


def test_read_preregistration_refuses_a_commit_without_the_artifact():
    """The shipped state: the commit resolves, the artifact is absent (#9075)."""
    record, why = probe.read_preregistration(
        HEAD, Path("docs/evidence/7949-capstone/preregistration.json"))
    assert record is None
    assert "does not carry" in why
    assert "#9075" in why


def test_backend_must_be_searchable():
    ok, why = probe.backend_is_searchable(None)
    assert not ok and "unset" in why
    ok, why = probe.backend_is_searchable("embedded://")
    assert not ok and "embedded" in why
    ok, _why = probe.backend_is_searchable("docker://:pw@localhost:6379/g")
    assert ok


def test_a_missing_model_key_is_refused_not_measured_as_an_empty_extraction(monkeypatch):
    for key in ("TORTOISE_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "TORTOISE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    ok, why = probe.model_key_present()
    assert not ok
    assert "#3892" in why
