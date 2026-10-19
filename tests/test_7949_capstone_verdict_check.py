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
import re
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
    """Read a revision WITHOUT assuming history or a resolvable parent.

    ``check=False`` on purpose: CI checks this repo out at ``fetch-depth: 1``,
    so only ``HEAD`` is guaranteed to resolve. A ``check=True`` call on a
    revision the shallow clone does not have raises ``CalledProcessError`` at
    MODULE IMPORT — i.e. a collection error on every PR (no `FAILED <nodeid>`
    for the merge rail to attribute) — which is exactly what a
    ``git rev-parse HEAD~1`` here did. Anything needing two commits builds its
    own pair (``_ancestry_pair``); nothing derives ancestry from the checkout.
    """
    return subprocess.run(["git", *args], cwd=str(_REPO), capture_output=True,
                          text=True, check=False).stdout.strip()


HEAD = _git("rev-parse", "HEAD")


def _ancestry_pair(tmp_path: Path) -> tuple[Path, str, str]:
    """A throwaway repo with two commits and the cited evidence file.

    Returns ``(repo, parent_sha, child_sha)``. This is what makes the
    "evidence predates the pre-registration" case real (a genuine
    non-ancestor pair) without depending on THIS checkout's history depth.
    """
    repo = tmp_path / "ancestry"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for key, value in (("user.email", "capstone@example.invalid"),
                       ("user.name", "capstone"),
                       ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo), "config", key, value],
                       check=True)
    evidence = repo / "docs/evidence/7949-capstone/capstone.log"
    evidence.parent.mkdir(parents=True)
    # A REAL capstone log: the refusal rows cite it, and V6 requires the cited
    # file to carry their row (so a placeholder text file would red on V6 for
    # the four rows this test is not about).
    cited = {"schema": cvc.SCHEMA,
             "rows": [{"criterion": f"E{i}"} for i in range(1, 6)]}
    evidence.write_text(f"{cvc.BEGIN}\n{json.dumps(cited)}\n{cvc.END}\n")

    def commit(message: str) -> str:
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q",
                        "--allow-empty", "-m", message], check=True)
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()

    return repo, commit("parent"), commit("child")


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
    findings = cvc.check(record, repo=_REPO, require_green=require_green)
    return [code for code, _w, _m in findings.items]


# ── the shipped evidence ──────────────────────────────────────────────────

def test_shipped_capstone_log_is_structurally_clean():
    """The committed log parses and passes every structural/honesty rule.

    This is the pin on the artifact: if the shipped table is regenerated with a
    row whose evidence does not exist, or without a blocker, THIS reds — the
    evidence bundle cannot quietly rot the way §10's prose did.
    """
    record = cvc.extract_block(LOG.read_text())
    findings = cvc.check(record, repo=_REPO, require_green=False)
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


def _messages(record: dict) -> list[str]:
    return [m for _c, _w, m in
            cvc.check(record, repo=_REPO, require_green=False).items]


def test_duplicate_row_is_a_finding():
    """A DUPLICATE, with no criterion missing, so only the `n > 1` arm can fire.

    Non-vacuity (#9080 review F3): the old version relabelled row 2 to "E1",
    which ALSO deleted E2 — so the `n == 0` "row is MISSING" arm reddened the
    test no matter what the duplicate arm did. Appending a sixth row keeps
    5/5 present and the assertion now names the duplicate branch's own text.
    """
    rec = _record()
    rec["rows"].append(dict(rec["rows"][0]))
    msgs = _messages(rec)
    assert any("2 rows" in m and "decided once" in m for m in msgs), msgs


def test_unknown_criterion_is_a_finding():
    """An UNKNOWN id, with no criterion missing — the same vacuity, fixed.

    Non-vacuity: the old version relabelled E5 to "E6", which deleted E5 and let
    the missing-row arm satisfy the assertion. A sixth row keeps 5/5 present, so
    only the "not an E1–E5 criterion" branch can produce this finding.
    """
    rec = _record()
    rec["rows"].append(dict(rec["rows"][4], criterion="E6"))
    msgs = _messages(rec)
    assert any("not an E1–E5 criterion" in m for m in msgs), msgs


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


def test_green_with_zero_numerator_is_a_finding():
    """0 successes is the SILENT-DROP shape, never a green measurement.

    Non-vacuity (#9080 review F3): the documented rule is ``0 < numerator``, the
    code accepted ``0 <= numerator``, and no test covered 0 — so the two halves
    could disagree without a red. ``_s4_merge``'s ``kept_from_s2=0`` of 15 is
    exactly the total silent drop E4's row exists to refuse.
    """
    rec = _record()
    _green(rec)
    rec["rows"][0]["numerator"] = 0
    msgs = _messages(rec)
    assert any("at least ONE success" in m for m in msgs), msgs


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


def test_green_row_whose_evidence_predates_the_preregistration_is_a_finding(
        tmp_path):
    """`0 rows green where the evidence artifact predates the pre-registration`.

    The pair is built in a throwaway repo (parent, then child): the row is
    measured at the PARENT while the thresholds were frozen at the CHILD, so
    the evidence predates them by construction. Before this, the test read
    ``git rev-parse HEAD~1`` at MODULE IMPORT, which does not resolve in CI's
    depth-1 checkout and turned the whole file into a collection error on
    every PR.
    """
    repo, parent, child = _ancestry_pair(tmp_path)
    rec = _record()
    _green(rec)
    rec["prereg"]["sha"] = child
    rec["rows"][0]["sha"] = parent        # measured BEFORE the thresholds froze
    findings = cvc.check(rec, repo=repo, require_green=False)
    codes = [code for code, _w, _m in findings.items]
    assert codes == ["V5"], findings.render()
    assert "PREDATES" in findings.render()


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
#
# TWO fixtures, deliberately, because there are two different objects and
# conflating them is what made the original observer tests vacuous (#9080
# review F2/P1). ``_extractor_result`` is ``extractor_v2.extract_session_v2``'s
# OWN return; ``_receipt`` is what ``TortoiseSDK.capture_session`` — the entry
# point the probe actually drives — hands back. The probe holds the SECOND one,
# and it carries ``stats`` only (#9116).
def _extractor_result(**over) -> dict:
    """``extract_session_v2``'s return — NOT what the probe is handed."""
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


def _receipt(**over) -> dict:
    """``capture_session``'s receipt, VERBATIM from ``tortoise/sdk.py``.

    The keys are the ``resp`` literal the SDK builds (minus the additive
    protocol keys, which merge flat and add no pipeline surface). A test that
    shapes this fixture differently from that literal is asserting a
    contract the caller can never satisfy — which is the vacuity this file
    was caught in.
    """
    result = {
        "session_id": "capstone_abc123",
        "turns": 1,
        "extracted": 15,
        "points": [],
        "extraction_mode": "v2",
        "ok": True,
        "errors": [],
        "warnings": [],
        "diagnostics": [],
        "stats": {"edus": 12, "chunks": 1,
                  "llm": {"by_stage": {"s1": {"calls": 1},
                                       "s2": {"calls": 1}}},
                  "s4_merge": {"s2_items": 15, "kept_from_s2": 15,
                               "merged_items": 15, "s4_items": 2,
                               "corrected_by_s4": 0,
                               "verbatim_reemissions": 0, "added_by_s4": 0}},
        "capture_redactions": 0,
    }
    for k, v in over.items():
        if v is None:
            result.pop(k, None)
        else:
            result[k] = v
    return result


def test_all_eight_steps_are_observable_when_the_full_surface_is_passed():
    """The observer CAN map eight steps — given ``extract_session_v2``'s return.

    This is the capability half, and it is NOT evidence about the shipped run:
    the probe is handed ``capture_session``'s receipt (next test). Kept because
    the day #9116 lands a seam, this is the shape it must carry.
    """
    seen = probe.observe_steps(_extractor_result())
    assert set(seen) == set(cvc.STEPS), sorted(set(cvc.STEPS) - set(seen))
    assert all(isinstance(v, str) and v for v in seen.values())


def test_the_shipped_receipt_observes_exactly_the_four_steps_it_carries():
    """#9116, pinned: on the REAL receipt four steps are unobservable.

    Non-vacuity: this is the assertion the old suite lacked. Pass the receipt
    (what ``capture_session`` returns) and the four off-receipt witnesses are
    absent; re-add any of them to the receipt and the equality reds, which is
    what makes ``UNOBSERVABLE_STEPS`` a measurement rather than a comment.
    """
    seen = probe.observe_steps(_receipt())
    assert set(seen) == {"S0", "S1", "S2.1", "S5"}, sorted(seen)
    assert set(probe.UNOBSERVABLE_STEPS) == set(cvc.STEPS) - set(seen)
    # and NOT one of the unobservable steps is quietly satisfied by a
    # same-named key the receipt happens to carry
    assert "extraction" not in probe.observe_steps.__code__.co_consts


def test_every_witness_observe_steps_reads_is_on_the_receipt_or_declared():
    """The contract between the observer and the entry point it reads (#9116).

    Derives BOTH sides from source, so neither can drift behind a comment:
      * the READ set — every ``result.get("X")`` / ``result["X"]`` in
        ``observe_steps`` — must equal ``probe.WITNESS_KEYS``;
      * the RECEIPT set — the keys of the ``resp = {...}`` literal in
        ``TortoiseSDK.capture_session`` — must cover every witness key that is
        not explicitly declared off-receipt, and must NOT contain one that is.

    Mutations that red it: add an undeclared ``result.get("foo")``; drop
    ``stats`` from the receipt literal; declare a key off-receipt that the
    receipt does in fact carry (over-declaring would hide a real witness).
    """
    import ast

    tree = ast.parse(PROBE.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef,))
              and n.name == "observe_steps")
    read: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or len(node.args) != 1:
            continue
        target, key = node.func.value, node.args[0]
        if (isinstance(target, ast.Name) and target.id == "result"
                and isinstance(key, ast.Constant) and isinstance(key.value, str)):
            read.add(key.value)
    assert read == set(probe.WITNESS_KEYS), (
        sorted(read ^ set(probe.WITNESS_KEYS)), "observer read set != declared")

    sdk_src = (_REPO / "tortoise/sdk.py").read_text()
    sdk_tree = ast.parse(sdk_src)
    capture = next(n for n in ast.walk(sdk_tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "capture_session")
    resp_literals = [n.value for n in ast.walk(capture)
                     if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "resp"
                             for t in n.targets)
                     and isinstance(n.value, ast.Dict)]
    assert len(resp_literals) == 1, (
        "capture_session's receipt literal moved or multiplied — re-derive "
        "this contract, do not delete it")
    receipt_keys = {k.value for k in resp_literals[0].keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    # ``**({...} if cond else {})`` — the SDK adds ``report_url`` conditionally.
    # Follow every dict-unpack expression and take its own string keys, so a
    # conditional key is COUNTED rather than silently skipped.
    for key, value in zip(resp_literals[0].keys, resp_literals[0].values,
                          strict=True):
        if key is not None:
            continue
        for nested in ast.walk(value):
            if isinstance(nested, ast.Dict):
                receipt_keys |= {k.value for k in nested.keys
                                 if isinstance(k, ast.Constant)
                                 and isinstance(k.value, str)}

    off = set(probe.WITNESS_KEYS_OFF_RECEIPT)
    assert off <= set(probe.WITNESS_KEYS), "off-receipt keys must be witnesses"
    assert not (off & receipt_keys), (
        sorted(off & receipt_keys),
        "declared OFF the receipt but the receipt carries it — over-declaring "
        "hides a witness #9116 may already have fixed")
    assert set(probe.WITNESS_KEYS) - off <= receipt_keys, (
        sorted((set(probe.WITNESS_KEYS) - off) - receipt_keys),
        "the observer reads a key the receipt does not carry, and it is not "
        "declared off-receipt")
    # the dead branch the review found: `capture_session` returns no such key
    assert "extraction" not in receipt_keys


def test_a_vet_disabled_pass_does_not_observe_s2_2():
    """The flag-off arm runs no S2.2 — so its table cannot claim S2.2."""
    seen = probe.observe_steps(_extractor_result(vet={"enabled": False, "s2": {}}))
    assert "S2.2" not in seen


def test_a_classify_disabled_pass_does_not_observe_s2_3():
    seen = probe.observe_steps(_extractor_result(
        classify_later={"enabled": False, "s2": {}, "union": {}}))
    assert "S2.3" not in seen


def test_a_pass_without_a_payload_does_not_observe_s6():
    seen = probe.observe_steps(_extractor_result(payload=None))
    assert "S6" not in seen


def test_a_g5_refusal_blames_the_instrument_not_the_pipeline(tmp_path, monkeypatch):
    """#9116: the refusal must be attributed to the surface, not to the pipeline.

    A bare "steps not observed" reads as "the extractor skipped VET/classify/
    search/commit" — a product finding this probe cannot support, and the one
    reading that would send the epic's readers to #5199/#9077/#7947. The
    non-vacuity: the pre-fix reason named neither #9116 nor the instrument.
    """
    import tortoise.sdk as sdk_mod

    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    _read_prereg_ok(monkeypatch, _frozen_prereg(
        corpus={"sha256": probe.corpus_sha256(corpus), "items": 1}))
    monkeypatch.setattr(probe, "backend_is_searchable", lambda: (True, ""))
    monkeypatch.setattr(probe, "model_key_present", lambda: (True, ""))

    class _FakeSDK:
        def capture_session(self, _turns, session_id=None):
            return _receipt()

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)
    out = tmp_path / "g5.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 5, code
    reason = cvc.extract_block(out.read_text())["run"]["reason"]
    assert "#9116" in reason, reason
    assert "INSTRUMENT gap" in reason, reason
    for step in probe.UNOBSERVABLE_STEPS:
        assert step in reason, (step, reason)
    assert "NOT the pipeline" in reason, reason


def test_a_green_branch_is_unreachable_until_9116_lands(tmp_path, monkeypatch):
    """G6 is dead code by construction while the receipt carries no witnesses.

    This is the fact the bundle must not hide: no run of this probe can emit a
    COMPLETED table, so no row can ever be GREEN — even with every dependency
    closed. Pinned so the day #9116 lands the seam this test reds and forces
    the README/Reproduce update with it.
    """
    assert set(probe.UNOBSERVABLE_STEPS), "the gap must be named"
    full = probe.observe_steps(_extractor_result())
    receipt = probe.observe_steps(_receipt())
    assert len(receipt) < len(full)
    assert set(cvc.STEPS) - set(receipt) == set(probe.UNOBSERVABLE_STEPS)


def test_e4_receipt_flags_the_four_edge_denominator():
    """E4's row fails when the denominator is 4 — the historical defect."""
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 4, "kept_from_s2": 4}})
    assert receipt["denominator_is_the_4_edge_defect"] is True
    ok = probe.e4_receipt({"s4_merge": {"s2_items": 15, "kept_from_s2": 14}})
    assert ok["denominator_is_the_4_edge_defect"] is False
    assert (ok["numerator"], ok["denominator"]) == (14, 15)


def test_the_e3_census_instrument_is_not_shipped_unwired():
    """E3's census is #5199's to land WITH its graph reader.

    A census function the shipped path never calls reads as coverage of the run
    while measuring nothing, and a reader written blind (a Cypher filter on an
    anchor property `#5199` says does not exist yet on `:Object`/`:Event`)
    would return zero rows and read as a working instrument reporting `(0, 0)`.
    So the probe carries no census; this pins that it does not come back
    without the reader that calls it.
    """
    assert not hasattr(probe, "e3_census_from_rows")


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
    findings = cvc.check(record, repo=_REPO,
                         require_green=False)
    assert not findings, findings.render()
    assert "V9" in _codes(record, require_green=True)


def _frozen_prereg(**over) -> dict:
    record = {"schema": probe.PREREG_SCHEMA, "written_before_any_run": True}
    record.update(over)
    return record


def _read_prereg_ok(monkeypatch, record: dict | None = None) -> None:
    """G0 PASSES, so the run reaches G1+ — the half no test used to reach."""
    fixed = record if record is not None else _frozen_prereg()
    monkeypatch.setattr(probe, "read_preregistration",
                        lambda _sha, _artifact: (fixed, ""))


def test_post_g0_refusals_record_the_preregistration_as_read(tmp_path, monkeypatch):
    """A G1–G5 refusal must not write `prereg.status: "ABSENT"`.

    Only the G0 refusal means "#9075 is open". Every later gate reached the run
    BECAUSE the frozen pre-registration was read, so the log has to say so —
    otherwise a G2/G5 refusal tells the next lane the thresholds are still
    missing and the table it leaves behind is wrong about the one dependency
    the whole chain hangs on.
    """
    _read_prereg_ok(monkeypatch)
    out = tmp_path / "missing-corpus.log"
    code = probe.main(["--prereg", HEAD,
                       "--corpus", str(tmp_path / "absent.json"),
                       "--out", str(out)])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert record["run"]["status"] == "REFUSED"
    assert record["prereg"]["status"] == "OK", record["prereg"]
    assert record["prereg"]["sha"] == HEAD


def test_a_g2_refusal_carries_the_corpus_it_read_and_the_preregistration(
        tmp_path, monkeypatch):
    """The corpus sha/turns are COMPUTED before G2, so the refusal carries them.

    A refusal that drops a sha it already computed leaves the log saying "no
    corpus" about a corpus whose bytes it hashed a line earlier.
    """
    _read_prereg_ok(monkeypatch)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    # G1 now fails CLOSED without a frozen corpus (#9080 review), so the record
    # must freeze the one this test feeds — otherwise the refusal happens one
    # gate earlier and this assertion would be reading the wrong gate's log.
    _read_prereg_ok(monkeypatch, _frozen_prereg(
        corpus={"sha256": probe.corpus_sha256(corpus), "items": 1}))
    out = tmp_path / "g2.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 3, code
    record = cvc.extract_block(out.read_text())
    assert record["run"]["gate_failed"] == 3
    assert record["prereg"]["status"] == "OK"
    assert record["corpus"]["sha256"] == probe.corpus_sha256(corpus)
    assert record["corpus"]["turns"] == 1


def test_a_g1_sha_mismatch_refusal_carries_the_computed_corpus_sha(
        tmp_path, monkeypatch):
    """The sha-mismatch refusal is ABOUT a sha — it must record the one it read."""
    _read_prereg_ok(monkeypatch, _frozen_prereg(
        corpus={"path": "corpus.json", "sha256": "f" * 64, "items": 1}))
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "g1.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert "sha256" in record["run"]["reason"]
    assert record["prereg"]["status"] == "OK"
    assert record["corpus"]["sha256"] == probe.corpus_sha256(corpus), (
        "the refusal discarded a sha it had already computed")


def test_root_is_the_repo_root():
    """`parents[3]` is `<repo>/docs`; every ROOT-joined path would be wrong."""
    assert probe.ROOT == _REPO, probe.ROOT


def test_the_readme_does_not_describe_the_retired_extraction_lookup():
    """The bundle must name the LIVE call path, not a construction it deleted.

    The README said `captured.get("extraction") or captured` hands
    `observe_steps` the receipt. That lookup was removed under review F1 (it is
    a DEAD lookup — `capture_session` returns no `extraction` key — and its
    fallback hid the shape mismatch), so the shipped probe binds
    `result = captured`. A bundle whose job is to be exact about what was and
    was not observed cannot present a retired expression as the live one.

    Non-vacuity: on the pre-fix README the first assertion reds (the dead
    expression was there) and the second reds too (`result = captured` was
    nowhere in it).
    """
    readme = (PROBE.parents[1] / "README.md").read_text()
    assert 'captured.get("extraction")' not in readme, (
        "the README names a lookup the probe deleted")
    assert "result = captured" in readme, (
        "the README must name the binding the probe actually uses")


def test_the_durations_row_quotes_the_test_count_it_weights():
    """The shard weight's provenance must describe the file that SHIPS.

    #9080 review: the `durations` row carried `MEASURED … (40 tests): 2.26s`
    for a file that had grown to 51 — a measurement of a smaller file used as
    the input to shard packing, and a body sentence that could not be
    reproduced (`pytest` cannot report `40 passed` for 51 tests). The count is
    mechanical, so it is pinned here: change the file's test count and the
    comment must move with it.

    Non-vacuity: at the pre-fix head the comment said 40 while the file held
    51, so this reds.
    """
    manifest = (_REPO / "config" / "ci-surfaces.yml").read_text()
    row = next((ln for ln in manifest.splitlines()
                if ln.strip().startswith(
                    "test_7949_capstone_verdict_check.py:")), None)
    assert row is not None, "the durations row is the weight this test guards"
    quoted = re.search(r"(\d+)\s+tests", row)
    assert quoted, f"the row must quote its measured test count: {row}"
    shipped = len(re.findall(r"^def test_",
                             (Path(__file__)).read_text(), re.MULTILINE))
    assert int(quoted.group(1)) == shipped, (
        f"the durations row quotes {quoted.group(1)} tests; the file ships "
        f"{shipped} — re-measure and move the count and the weight together")


def test_the_manifest_names_only_arms_the_file_is_registered_in():
    """A comment that points at a row that does not exist sends a lane nowhere.

    #9080 review: the `core` block told the reader to "see the `eval` arm" —
    there is no `eval` arm for this file (it is in `core`, `tier1` and
    `durations` only), and the docs-only reason lives in the `tier1` arm. The
    manifest's comments ARE the rationale of record, so a dangling
    cross-reference is a defect in the artifact, not a typo.

    Non-vacuity: the `eval` reference was live at the pre-fix head, so this
    reds on it.
    """
    lines = (_REPO / "config" / "ci-surfaces.yml").read_text().splitlines()
    entries = [i for i, ln in enumerate(lines)
               if ln.strip() == "- test_7949_capstone_verdict_check.py"]
    assert entries, "the file must be registered in at least one surface arm"
    blocks: list[list[str]] = []
    for i in entries:
        block = [lines[i]]
        j = i - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            block.append(lines[j])
            j -= 1
        blocks.append(block)
    dangling = [ln for block in blocks for ln in block if "`eval`" in ln]
    assert not dangling, (
        "the manifest cites an `eval` arm that does not exist for this file: "
        + repr(dangling[:2]))
    # and the durations row (the third home) must name a real one too
    dur = [ln for ln in lines
           if ln.strip().startswith("test_7949_capstone_verdict_check.py:")]
    assert len(dur) == 1, dur
    assert "`eval`" not in dur[0], dur[0]
    assert "`core`/`tier1`" in dur[0] or "tier1" in dur[0], dur[0]


def test_the_readme_names_the_run_s_own_blocker_not_only_the_rows(tmp_path):
    """#9080 review F1: the bundle named the ROWS' blockers and not the RUN's.

    The Reproduce block used to tell the next lane the real run needs exactly
    three things — `#9075`, a provider key, a searchable backend. At this head
    that is false and the fix commit says so: `capture_session`'s receipt
    carries no step surface, so `observe_steps` cannot witness
    S2.2/S2.3/S3/S6 and the run refuses at G5 for EVERY input once G0 lands
    (`#9116`). A bundle whose job is to stop a lane reading a claim as a
    measurement must not send that lane into a REFUSED table with the wrong
    cause in hand, and the gap is only visible in a commit message otherwise.

    Non-vacuity: this reds on the pre-fix README — `#9116` appeared nowhere in
    it (the four unwitnessable steps were named only in the fix commit), so
    `"#9116" in readme` was False and the Reproduce block asserted no
    precondition beyond `#9075`.
    """
    readme = (PROBE.parents[1] / "README.md").read_text()
    assert "#9116" in readme, (
        "the bundle must name the run's own blocker: S2.2/S2.3/S3/S6 are not "
        "observable on capture_session's receipt, so G5 refuses for every input")
    # the Reproduce block specifically — a mention in prose elsewhere would not
    # stop the operator following the copy-paste command into a refused run.
    reproduce = readme.split("## Reproduce", 1)
    assert len(reproduce) == 2, "the Reproduce section is what an operator copies"
    assert "#9116" in reproduce[1], (
        "the Reproduce command's own comment must carry the #9116 precondition")
    # and the step names, so the reader can see WHICH witnesses are missing
    for step in ("S2.2", "S2.3", "S3", "S6"):
        assert step in readme, step


def test_the_parser_declares_no_flag_the_run_ignores(tmp_path):
    """A documented flag the run never reads is a claim with no mechanism.

    `--e1e2-evidence` / `--e5-evidence` were accepted and ignored, while the
    README told the operator to pass them as the way E1/E2/E5 get their
    evidence. The artifacts' formats are owned by `#9076` / `#7947`, so the
    flags land with them — this pins that no dead flag (nor a README line
    telling an operator to use one) comes back first.

    `--head` is the counter-example that must NOT be dropped: it IS read, it is
    validated before it can reach a shipped log (see the `--head` tests below),
    and the README documents the single case it exists for.
    """
    parser = probe.build_parser()
    help_text = parser.format_help()
    readme = (PROBE.parents[1] / "README.md").read_text()
    assert "--e1e2-evidence" not in help_text
    assert "--e5-evidence" not in help_text
    assert "--e1e2-evidence" not in readme
    assert "--e5-evidence" not in readme
    import pytest
    with pytest.raises(SystemExit):
        parser.parse_args(["--prereg", HEAD, "--corpus", str(tmp_path),
                           "--e1e2-evidence", "x.json"])


def test_a_free_text_head_override_is_refused_before_it_is_stamped(tmp_path):
    """`--head` stamps EVERY row and V5 resolves the prereg against it.

    The pre-fix run stamped whatever string it was given: `head = args.head or
    rev-parse HEAD`, with no format check and no `git cat-file` resolution. That
    makes the provenance chain rest on free text — the log's `head`, every
    row's `sha`, and the ancestry rule that decides whether the evidence
    predates the thresholds it claims to validate. Refused BEFORE G0, because
    no pre-registration can make an unusable measured commit meaningful.

    Non-vacuity: on the pre-fix probe this returns the G0 refusal (code 2, the
    artifact is absent at HEAD) and writes `# head: not-a-sha` — the assertions
    on the reason and on the emitted head both red against it.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "bad-head.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out), "--head", "not-a-sha"])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert "--head" in record["run"]["reason"], record["run"]["reason"]
    assert "not a commit sha" in record["run"]["reason"]
    assert record["head"] != "not-a-sha", (
        "an unvalidated override reached the shipped record")
    assert record["head"] == "unknown"
    assert {row["sha"] for row in record["rows"]} == {"unknown"}


def test_a_head_that_does_not_resolve_is_refused(tmp_path):
    """A well-FORMED sha that resolves to nothing is still not a measurement.

    `--head 000...0` passes a format check and fails `git cat-file`; accepting
    it would write a sha into the evidence file that no commit in this repo
    carries, which V5's ancestry test can then only ever refuse for the wrong
    reason. Non-vacuity: the pre-fix probe stamped it verbatim.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "unresolved-head.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out), "--head", "0" * 40])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert "does not resolve" in record["run"]["reason"]
    assert record["head"] == "unknown"


def test_a_resolvable_head_override_is_what_the_rows_are_stamped_with(tmp_path):
    """The legitimate use stays legitimate: a real commit IS the measured head.

    Without this control, the validation above could be satisfied by refusing
    every override, which would delete the only reason `--head` exists (a
    re-run against a tagged commit that is not the worktree's HEAD).
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "good-head.log"
    resolved = _git("rev-parse", HEAD)
    assert probe.HEAD_SHA_RE.match(resolved), resolved
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out), "--head", resolved])
    assert code == 2, code          # still G0: #9075 has not landed
    record = cvc.extract_block(out.read_text())
    assert record["head"] == resolved
    assert {row["sha"] for row in record["rows"]} == {resolved}
    assert "--head" not in record["run"]["reason"]


def test_the_readme_documents_the_head_override_it_exposes():
    """A flag that changes provenance must be legible in the artifact of record.

    `--head` was absent from the module docstring's `Run:` block and from the
    README's Reproduce block, so a reader could not tell whether a shipped
    log's `head` was the measured commit or an override (#9080 review).
    """
    readme = (PROBE.parents[1] / "README.md").read_text()
    assert "--head" in readme, "the Reproduce note must name the override"
    assert "--head" in probe.__doc__


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


def test_backend_must_be_searchable(monkeypatch):
    """G2 decides with `extractor_v2`'s gate, not a string heuristic.

    Non-vacuity, both directions (#9080 review, P1): on the pre-fix code
    ``backend_is_searchable("/tmp/graph.db")`` returned True — a file path is
    NOT a supported scheme, so `config.is_db_uri` rejects it and the SDK treats
    that store as EMBEDDED and skips S3, i.e. the gate accepted verbatim the
    lane it exists to exclude. And a hosted lane (``TORTOISE_API_URL``, no DB
    URI) was refused. Both are pinned below.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    ok, why = probe.backend_is_searchable()
    assert not ok and ("unset" in why or "embedded" in why)

    # the fail-open the review found: a file path is the EMBEDDED store
    for not_a_backend in ("/tmp/graph.db", "~/.tortoise/tortoise.db",
                          "embedded://", "x://y", "none"):
        monkeypatch.setenv("TORTOISE_DB_URI", not_a_backend)
        ok, why = probe.backend_is_searchable()
        assert not ok, (not_a_backend, why)
        assert "embedded" in why, why

    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@localhost:6379/g")
    ok, why = probe.backend_is_searchable()
    assert ok, why
    monkeypatch.setenv("TORTOISE_DB_URI", "redis://localhost:6379/g")
    assert probe.backend_is_searchable()[0]

    # the false refusal the review found: the hosted lane IS searchable
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_API_URL", "https://api.example.invalid")
    ok, why = probe.backend_is_searchable()
    assert ok, why


def test_a_missing_model_key_is_refused_not_measured_as_an_empty_extraction(monkeypatch):
    """G3 asks the adapter's question, not a hand-written key list.

    Non-vacuity (#9080 review): the pre-fix list accepted ``OPENAI_API_KEY`` /
    ``ANTHROPIC_API_KEY`` / ``TORTOISE_LLM_API_KEY`` — keys `_extract_session_v2`
    cannot consume (#1530), so such a host passed G3 and died at G4 blaming the
    entry point — and it refused a ``TORTOISE_SESSION_LLM_MOCK=1`` host, which
    would have run. Both directions are pinned.
    """
    for key in ("TORTOISE_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "TORTOISE_API_KEY",
                "VENICE_API_KEY", "TORTOISE_EXTRACTOR_PROVIDER",
                "TORTOISE_SESSION_LLM_MOCK"):
        monkeypatch.delenv(key, raising=False)
    ok, why = probe.model_key_present()
    assert not ok
    assert "#3892" in why

    # keys the adapter CANNOT consume must not pass the gate
    for unusable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                     "TORTOISE_LLM_API_KEY", "TORTOISE_API_KEY"):
        monkeypatch.setenv(unusable, "sk-test")
        ok, why = probe.model_key_present()
        assert not ok, (unusable, why)
        monkeypatch.delenv(unusable, raising=False)

    # the keys it CAN consume pass
    for usable in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "VENICE_API_KEY"):
        monkeypatch.setenv(usable, "sk-test")
        assert probe.model_key_present()[0], usable
        monkeypatch.delenv(usable, raising=False)

    # the offline seam (#1468) the repo's e2e lanes run must not be refused
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    assert probe.model_key_present()[0]
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK")

    # explicit-but-keyless fails CLOSED, with the adapter's own reason
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "openrouter")
    ok, why = probe.model_key_present()
    assert not ok and "explicit" in why, why


def test_a_prereg_that_freezes_no_corpus_is_refused_not_a_no_op(tmp_path, monkeypatch):
    """G1 must fail closed when the pre-registration carries no corpus block.

    Non-vacuity (#9080 review): both G1 checks used to be conditional, so a
    pre-registration freezing thresholds but no corpus made G1 a no-op — the run
    measured whatever corpus it was handed and stamped that sha on every row as
    the pre-registered one. V7 cannot see it (row sha vs the log's own sha are
    self-consistent by construction), so the refusal has to happen here.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    _read_prereg_ok(monkeypatch, _frozen_prereg())  # thresholds, NO corpus
    out = tmp_path / "no-corpus.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert "corpus.sha256" in record["run"]["reason"], record["run"]["reason"]
    assert record["prereg"]["status"] == "OK"
    assert record["corpus"]["sha256"] == probe.corpus_sha256(corpus)
