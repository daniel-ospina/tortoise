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
(V1–V11) and a test that reds if the gate stops catching it.
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


def _codes(record: dict, *, require_green: bool = False,
           require_resolvable: bool = False) -> list[str]:
    findings = cvc.check(record, repo=_REPO, require_green=require_green,
                         require_resolvable=require_resolvable)
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
    """Every non-green row carries its cause — the issue's explicit demand.

    #5199 (E3 anchoring) and #9077 (E4 real path) are the per-criterion lanes;
    #9075 gates E1/E2's thresholds. E5 is NOT #7947 any more: that falsifier is
    CLOSED COMPLETED (2026-10-19T12:54:38Z), so citing it pointed every reader of
    the table at a landed issue — the misattribution #9089's review measured on
    two commits that re-touched the README AFTER the closure while still printing
    OPEN. What E5 lacks is a PRE-REGISTERED pass to re-derive it in, which is the
    same G0 gate as E1/E2.
    """
    record = cvc.extract_block(LOG.read_text())
    for row in record["rows"]:
        assert row["blocked_by"].startswith("#"), row
    owners = {r["criterion"]: r["blocked_by"] for r in record["rows"]}
    assert owners == {"E1": "#9075", "E2": "#9075", "E3": "#5199",
                      "E4": "#9077", "E5": "#9075"}, owners
    # The LANDED lane is named in the note, not dropped: a reader must be able to
    # tell "measured, needs re-deriving" from "nobody has run this".
    e5 = {r["criterion"]: r for r in record["rows"]}["E5"]
    assert "#7947 LANDED" in e5["note"], e5["note"]
    assert "CLOSED COMPLETED" in e5["note"], e5["note"]


def test_the_probes_owners_map_names_no_landed_criterion_lane():
    """REGRESSION (#9089 review): a row's OWNER must be OPEN work.

    E5 named #7947 after it closed, and the README's own dependency table printed
    that issue as OPEN two commits after it was closed — a stale value nothing
    reds on, because V6 accepts any `#NNN` with no state check (the gate is
    offline by design). The pin is therefore on the MAP: a criterion lane that
    has landed belongs in LANDED_LANES, and its owner is whatever OPEN work is
    actually left.

    Non-vacuity: with `OWNERS["E5"] = "#7947"` the first assert fails, and the
    note suffix this test also pins did not exist at all.
    """
    assert "E5" not in [c for c, lane in probe.OWNERS.items()
                        if lane in probe.LANDED_LANES.values()], probe.OWNERS
    assert probe.LANDED_LANES == {"E5": "#7947"}, probe.LANDED_LANES
    rows = {r["criterion"]: r for r in probe.refused_rows(
        "gate refused: x", prereg_ok=False)}
    assert rows["E5"]["blocked_by"] == "#9075", rows["E5"]
    assert "#7947 LANDED" in rows["E5"]["note"], rows["E5"]["note"]
    # and E1/E2's ALSO_BLOCKED_BY note is not disturbed by the criterion-lane
    # suffix. (#9075's own LANDED note DOES reach E1's note once `prereg_ok` —
    # it is the gate behind every row — so the assertion names the E5-only
    # suffix rather than the bare word; see
    # `test_a_row_that_blames_the_landed_pre_registration_says_it_landed`.)
    assert "the measurement itself is #9076" in rows["E1"]["note"]
    assert "#7947 LANDED" not in rows["E1"]["note"]


# ── V1/V3: the record's SHAPE and its vocabulary ──────────────────────────

def test_an_unrecognised_root_schema_is_a_finding():
    """V1: a schema this gate does not know is refused, never guessed at.

    #9089 review P2: this arm was pinned by NOTHING — deleting the schema test
    from `check()` left the whole suite green, so a refactor could drop it and
    the gate would go on blessing a `capstone-verdict-v2` table it cannot read
    (the same shape `capstone_preregistration_check` already refuses in the
    exemption, which is why the two must not disagree here).
    """
    codes = _codes(_record(schema="capstone-verdict-v2"))
    assert "V1" in codes, codes


def test_an_unrecognised_verdict_word_or_evidence_kind_is_a_finding():
    """V3: the vocabulary is closed. An unknown word is not a verdict.

    #9089 review P2: `VERDICTS` and `EVIDENCE_KINDS` were likewise unpinned —
    neutering both kept 95/95 green. The concrete failure they prevent: a row
    carrying `"verdict": "PASS"` produced no V3 finding, fell through to the
    blocker rule (a REFUSED row with `blocked_by` set is honest) and the table
    exited 0 "structurally sound" with a verdict word the gate does not
    understand — a green light spelled a new way.
    """
    rec = _record()
    rec["rows"][0]["verdict"] = "PASS"
    rec["rows"][0]["evidence_kind"] = "estimate"
    codes = _codes(rec)
    assert "V3" in codes, codes


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
    # A green row rests on a pre-registration the run READ: the block says so.
    rec["prereg"] = {"sha": rec["prereg"]["sha"], "status": "OK",
                     "artifact": rec["prereg"]["artifact"]}
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


def test_green_with_boolean_counts_is_a_finding():
    """A JSON ``true`` is not an integer count (#9089 review, P2).

    ``bool`` subclasses ``int``, so the pre-fix ``isinstance(num, int)`` arm
    accepted ``"numerator": true, "denominator": true`` and ``0 < True <= True``
    then passed the range arm: the gate read a malformed block as a GREEN
    *measurement* (reproduced: ``_codes`` empty, ``--require-green`` exit 0),
    which is the one wrong-typed shape this file's contract turned the wrong
    way. Both halves are asserted, because either alone still admits a count
    of one boolean and one real number.
    """
    for field in ("numerator", "denominator"):
        rec = _record()
        _green(rec)
        rec["rows"][0][field] = True
        assert "V5" in _codes(rec), field
    rec = _record()
    _green(rec)
    rec["rows"][0].update(numerator=1, denominator=True)
    assert "V5" in _codes(rec), "a true denominator is not a denominator"


def test_e4_measured_with_a_boolean_denominator_is_a_finding():
    """The same bool-is-an-int hole on V10's row arm (#9089 review, P2).

    V10 asks whether E4's measured denominator is a positive count; with
    ``denominator: true`` that question was answered ``True > 0``, so the one
    row carrying numbers could be measured against a boolean and the tally
    still printed "denominators agree".
    """
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                           "kept_from_s2": 14}})
    rec = _completed_record(receipt)
    e4 = next(r for r in rec["rows"] if r["criterion"] == "E4")
    e4["denominator"] = True
    rec["run"]["e4_receipt"]["denominator"] = True
    msgs = _messages(rec)
    assert any("no positive" in m for m in msgs), msgs


def test_a_measurement_row_without_a_corpus_is_a_finding_without_being_green_or_red():
    """V7 applies to what a row MEASURED, not to the verdict word (#9089 cycle 8).

    The arm fired only on GREEN/RED, so a row declaring `evidence_kind:
    "measurement"` under a non-GREEN verdict — exactly the shape
    `capstone_run.py::completed_rows` writes for four of five rows — carried no
    corpus obligation: a null `corpus_sha256` against a log that identified a
    corpus produced NO finding and the tally printed "structurally sound".
    Non-vacuity: the same row as RED yields V7, which is what made the gap
    invisible.
    """
    rec = _record()
    rec["rows"][0].update({"verdict": "BLOCKED",
                           "evidence_kind": "measurement",
                           "corpus_sha256": None})
    codes = _codes(rec)
    assert "V7" in codes, codes


def test_a_receipt_with_no_denominator_key_does_not_back_the_rows_number():
    """V10: absence is the same non-agreement as a wrong value (#9089 cycle 8).

    `15/15` on E4's row with a receipt that is present but carries no
    `denominator` key was SKIPPED by the `in receipt` guard, so the row's number
    stood unbacked while the gate reported one number deciding E4. The arm's own
    rule is that the row must carry the SAME denominator the receipt computed.
    """
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                           "kept_from_s2": 14}})
    rec = _completed_record(receipt)
    del rec["run"]["e4_receipt"]["denominator"]
    msgs = _messages(rec)
    assert any("carries no denominator" in m for m in msgs), msgs


def test_a_measured_e4_with_no_receipt_at_all_is_a_finding():
    """V10: absence covers the whole block, not just one key (#9089 review cycle 9).

    Tightening the arm for a receipt that lacks a `denominator` key left the
    broader shape open: deleting `run.e4_receipt` entirely (or making it a
    string) skipped the arm, so `15/15` stood with NO second copy and the gate
    still called the table sound. The probe always writes the block on its
    COMPLETED path, so only a hand-edited/newer-shape table reaches this — which
    is exactly the copy V10 exists to police.
    """
    for broken in (None, "15/15", [15]):
        receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                               "kept_from_s2": 14}})
        rec = _completed_record(receipt)
        if broken is None:
            del rec["run"]["e4_receipt"]
        else:
            rec["run"]["e4_receipt"] = broken
        msgs = _messages(rec)
        assert any("no second copy" in m for m in msgs), (broken, msgs)


def test_a_boolean_receipt_denominator_against_a_numeric_row_is_a_finding():
    """A copy that is not a number cannot AGREE with the row's (#9089 cycle 2).

    The agreement arm is the one place where a malformed value must not merely
    be excluded: the pre-helper code fired here (``True != 15``), and a fix that
    guards the comparison with ``isinstance``-style narrowing silently SKIPPED
    it — dropping a true-positive while reporting "denominators agree" about
    ``15`` and ``true``. So the row keeps its honest count and only the receipt
    copy is corrupted.
    """
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                           "kept_from_s2": 14}})
    rec = _completed_record(receipt)
    e4 = next(r for r in rec["rows"] if r["criterion"] == "E4")
    assert e4["denominator"] == 15, e4
    rec["run"]["e4_receipt"]["denominator"] = True
    msgs = _messages(rec)
    assert any("cannot agree" in m for m in msgs), msgs


def test_sibling_copies_disagree_when_one_is_a_boolean_count():
    """``True == 1`` must not read as two copies of one number (#9089 cycle 2).

    V8 compares carried denominators with ``!=`` on raw values, so a sibling row
    carrying ``true`` agreed with the other's ``1``. Pre-existing rather than a
    regression, but the same malformed-shape class the boolean refusal exists
    to close, at the comparison that decides whether a number was carried
    faithfully — so it is closed here too.
    """
    rec = _record()
    _green(rec)
    rec["rows"][1].update(verdict="GREEN", evidence_kind="measurement",
                          evidence=rec["rows"][0]["evidence"],
                          numerator=31, denominator=True, blocked_by=None,
                          corpus_sha256="a" * 64, sibling="E1")
    assert "V8" in _codes(rec), "true must not agree with the sibling's 50"
    # And the honest pair stays silent: the refusal is of the BOOLEAN, not of
    # the agreement arm.
    rec = _record()
    _green(rec)
    rec["rows"][1].update(verdict="GREEN", evidence_kind="measurement",
                          evidence=rec["rows"][0]["evidence"],
                          numerator=31, denominator=50, blocked_by=None,
                          corpus_sha256="a" * 64, sibling="E1")
    assert "V8" not in _codes(rec)


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
    # Every row is stamped with the ONE run head (V3 resolves each of them
    # against `repo`, so a row still carrying this checkout's HEAD would red on
    # V3 for the wrong reason), and that head predates the thresholds.
    for row in rec["rows"]:
        row["sha"] = parent
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
    """A GREEN row must be provable where the commit still exists.

    Resolution is REPORTED by default and FAILS only under
    `--require-resolvable` (see V11): the acceptance command stamps the BRANCH
    head, and main carries single-parent squashes, so requiring it by default
    would red this bundle's own shipped-log test on every PR after merge.
    """
    rec = _record()
    _green(rec)
    rec["rows"][0]["sha"] = "0" * 40
    codes = _codes(rec, require_resolvable=True)
    assert "V11" in codes, codes
    # not a finding by default — but it IS reported
    findings = cvc.check(rec, repo=_REPO, require_green=False)
    assert not findings, findings.render()
    assert findings.unresolved


def test_a_green_row_under_a_log_that_says_the_prereg_was_never_read_reds():
    """REGRESSION (#9089 review cycle 5): V5 read `prereg.sha` and never
    `prereg.status`, so a table that records `status: "ABSENT"` in its own
    block — denying the read the green verdict rests on — passed 5/5 GREEN and
    `--require-green` exited 0. The epic's closing verdict was granted on a log
    that says the pre-registration was never read.
    """
    rec = _record()
    _green(rec)
    for row in rec["rows"]:
        row.update(verdict="GREEN", evidence_kind="measurement",
                   evidence="docs/evidence/7949-capstone/capstone.log",
                   numerator=10, denominator=50, blocked_by=None,
                   corpus_sha256="a" * 64, sha=HEAD)
    rec["prereg"]["status"] = "ABSENT"
    codes = _codes(rec, require_green=True)
    assert codes.count("V5") == 5, codes
    assert "V9" not in codes, codes


def test_an_unreadable_log_shape_is_a_usage_error_not_a_verdict(tmp_path, capsys):
    """A block that parses but is not a table, and a non-UTF-8 byte, are exit 2.

    Both used to raise and surface as exit 1 — the code that means "the table
    has findings", which a lane reads as a verdict on the evidence.
    """
    for name, body in (
        ("not-a-table.log", f"x\n{cvc.BEGIN}\n[]\n{cvc.END}\n"),
        ("null-table.log", f"x\n{cvc.BEGIN}\nnull\n{cvc.END}\n"),
    ):
        p = tmp_path / name
        p.write_text(body)
        assert cvc.main([str(p)]) == 2, name
    raw = tmp_path / "bad-bytes.log"
    raw.write_bytes(b"x\n" + cvc.BEGIN.encode() + b"\n{\xff}\n" + cvc.END.encode() + b"\n")
    assert cvc.main([str(raw)]) == 2


def test_an_unreachable_prereg_sha_does_not_accuse_the_evidence_of_predating():
    """REGRESSION (#9089 review cycle 4): the prereg commit is the OTHER operand
    of the ancestry test, and `_is_ancestor` read git's exit 128 ("cannot
    answer") as "not an ancestor".

    Measured before the fix: rows at a REAL sha with `prereg.sha` absent from
    the object DB produced five `V5 — evidence PREDATES the pre-registration`
    findings, exit 1 — a false accusation about the evidence, with a cause the
    lane cannot remediate. It is now REPORTED as unreported provenance.
    """
    rec = _record()
    _green(rec)
    for row in rec["rows"]:
        row.update(verdict="GREEN", evidence_kind="measurement",
                   evidence="docs/evidence/7949-capstone/capstone.log",
                   numerator=10, denominator=50, blocked_by=None,
                   corpus_sha256="a" * 64, sha=HEAD)
    rec["prereg"]["sha"] = "d" * 40          # well-formed, not in this repo
    findings = cvc.check(rec, repo=_REPO, require_green=False)
    codes = [c for c, _w, _m in findings.items]
    assert "V5" not in codes, findings.render()
    assert ("prereg", "d" * 40, "not in this object DB") in findings.unresolved, \
        findings.unresolved
    assert "V11" in _codes(rec, require_resolvable=True)


def test_an_unreadable_repo_is_a_usage_error_not_a_verdict(tmp_path, capsys):
    """Exit 1 means "the table has findings" — a lane reads it as a verdict.

    An unreadable `--repo` used to reach `subprocess.run(cwd=…)` and raise
    FileNotFoundError, exiting 1 on a traceback.
    """
    log = tmp_path / "capstone.log"
    log.write_text(LOG.read_text())
    assert cvc.main([str(log), "--repo", str(tmp_path / "nope")]) == 2


def test_a_wrong_typed_nested_block_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    """REGRESSION (#9089 review): only the ROOT record was type-checked.

    `run` / `prereg` / `corpus` are read with `.get` UNCONDITIONALLY, so a truthy
    non-object raised `AttributeError: 'list' object has no attribute 'get'` out
    of `check()` and surfaced as **exit 1 with a traceback** — the code this
    tool reserves for "the table has findings". A lane that reads exit 1
    concludes the EVIDENCE is bad when the artifact was merely malformed, and no
    verdict was ever reached.

    Non-vacuity: measured on the pre-fix tool, every shape below raised out of
    `main()` (AttributeError) or out of the tally loop (TypeError) — the `== 2` /
    `== 1` assertions are unreachable there.
    """
    for key in ("corpus", "prereg", "run"):
        rec = _record()
        rec[key] = [1]          # truthy, not an object
        log = tmp_path / f"{key}-block.log"
        log.write_text(f"x\n{cvc.BEGIN}\n{json.dumps(rec)}\n{cvc.END}\n")
        assert cvc.main([str(log), "--repo", str(_REPO)]) == 2, key
        assert "not an object" in capsys.readouterr().err, key
    # `rows: 5` is V2's own finding (the table is five rows), so it is exit 1 —
    # but with a VERDICT on the table, not the TypeError the tally loop raised.
    rec = _record()
    rec["rows"] = 5
    log = tmp_path / "rows-block.log"
    log.write_text(f"x\n{cvc.BEGIN}\n{json.dumps(rec)}\n{cvc.END}\n")
    assert cvc.main([str(log), "--repo", str(_REPO)]) == 1
    assert "V2" in capsys.readouterr().err


def test_a_cited_block_that_is_not_a_table_is_a_V6_finding_not_a_traceback(tmp_path):
    """REGRESSION (#9089 review): `extract_block` is raw `json.loads`, so a
    cited file whose block is `[]` / `"x"` made `cited.get` raise AttributeError
    — not in the V6 except tuple — and escaped `check()` as a traceback.

    V6 reads a SECOND file (an operator-supplied artifact), which is exactly
    where the guard is easiest to miss: the shipped V6 test cites `README.md`,
    which has no markers and takes the `ValueError` arm.

    Non-vacuity: both shapes below raised AttributeError / TypeError pre-fix;
    each is now the SAME finding an unmarked prose file already produced.
    """
    cited_dir = tmp_path / "docs/evidence/7949-capstone"
    cited_dir.mkdir(parents=True)
    cases = {
        "empty-list.log": "[]",
        "a-string.log": '"x"',
        # a criterion that is a LIST is unhashable — the set comprehension in
        # V6 raised TypeError on it (the V2 census already guards this shape).
        "unhashable-criterion.log": json.dumps({"rows": [{"criterion": ["E5"]}]}),
    }
    for name, block in cases.items():
        (cited_dir / name).write_text(f"x\n{cvc.BEGIN}\n{block}\n{cvc.END}\n")
        rec = _record()
        for row in rec["rows"]:
            row["evidence"] = f"docs/evidence/7949-capstone/{name}"
        findings = cvc.check(rec, repo=tmp_path, require_green=False)
        codes = [c for c, _w, _m in findings.items]
        assert codes.count("V6") == 5, (name, findings.render())


def test_a_non_iterable_steps_observed_does_not_crash_a_green_row():
    """REGRESSION (#9089 review): `tuple(run.get("steps_observed") or ())` raised
    TypeError on a non-iterable, escaping as exit 1 on a traceback.

    Non-vacuity: `tuple(5)` raises, so the pre-fix `_codes` call errored instead
    of returning findings; a value that observes nothing is now the V5 finding
    it should be.
    """
    rec = _record()
    _green(rec)
    rec["run"]["steps_observed"] = 5
    codes = _codes(rec)
    assert "V5" in codes, codes


def test_an_unhashable_criterion_in_the_outer_row_is_a_V6_finding_not_a_traceback():
    """REGRESSION (#9089 review): the THIRD site of the unhashable-criterion shape.

    V2's census and V6's ``cited_ids`` both filter a ``criterion`` down to
    hashable scalars — but ``cid`` in ``if cid not in cited_ids`` is the OUTER
    row's value, used as a SET MEMBER, and it was never guarded. So a row whose
    criterion is ``["E5"]`` raised ``TypeError: unhashable type: 'list'`` out of
    ``check()``; ``main()`` has no handler, so the tool exited **1** — the code
    documented as "the table has findings", i.e. a verdict ON THE EVIDENCE —
    while the artifact was merely malformed. That is the exact failure mode the
    module's own comments call out on the two peer sites.

    Non-vacuity: `["E5"] not in {...}` raises, so on the pre-fix tool this call
    errored instead of returning findings (measured: ``TypeError: unhashable
    type: 'list'``). The row now gets the same "no cited log can carry it"
    finding the cited-log arm already produces for a row name that is not one.
    """
    rec = _record()
    rec["rows"][4]["criterion"] = ["E5"]
    findings = cvc.check(rec, repo=_REPO, require_green=False)
    v6 = [m for code, _w, m in findings.items if code == "V6"]
    assert v6, findings.render()
    assert "not a row name" in v6[0], findings.render()


def test_an_unhashable_sibling_does_not_crash_the_denominator_check():
    """REGRESSION (#9089 review): `by_id.get(sib)` with an unhashable sibling
    raised TypeError out of `check()` — exit 1 on a traceback.

    Non-vacuity: `dict.get(["E1"])` raises, so pre-fix this call errored; the
    shape is now V8's "sibling is not a row" finding.
    """
    rec = _record()
    rec["rows"][1]["sibling"] = ["E1"]
    codes = _codes(rec)
    assert "V8" in codes, codes


def test_a_green_table_with_no_frozen_prereg_reds_even_when_the_sha_is_absent():
    """REGRESSION (#9089 review cycle 3): the frozen-threshold rule needs no git.

    Making sha resolution report-only put the `prereg_sha is None` branch behind
    the resolvability guard, so a 5/5-green table with NO frozen
    pre-registration reported "structurally sound" (exit 0, even with
    --require-green) as soon as the row shas were absent-but-well-formed — the
    exact shape a depth-1 CI clone produces. A pre-registration that does not
    exist is not a thing git resolution can excuse.
    """
    rec = _record()
    _green(rec)
    for row in rec["rows"]:
        row.update(verdict="GREEN", evidence_kind="measurement",
                   evidence="docs/evidence/7949-capstone/capstone.log",
                   numerator=10, denominator=50, blocked_by=None,
                   corpus_sha256="a" * 64)
    rec["prereg"] = {"sha": None, "status": "ABSENT",
                     "artifact": "docs/evidence/7949-capstone/preregistration.json"}
    # the shas are well-formed but absent here, so ancestry cannot be answered
    for row in rec["rows"]:
        row["sha"] = "1234567890abcdef" + "0" * 24
    codes = _codes(rec, require_green=True)
    assert codes.count("V5") == 5, codes
    assert "V9" not in codes, codes      # the rows ARE green; the V5s are the defect


def test_a_NON_GREEN_row_whose_sha_is_unresolvable_is_still_RESOLVED_ON_REQUEST():
    """REGRESSION (#9089 review). The resolve check lived inside the GREEN
    branch, so it ran on ZERO rows of the shipped table — which is 0/5 green.

    Non-vacuity: five rows stamped ``"0"*40`` returned no findings at all
    before the fix, while the tool printed "5/5 rows carry an evidence
    artifact + sha". A row whose stamped commit cannot be resolved has
    provenance nobody can re-derive, whatever its verdict says.
    """
    rec = _record()
    for row in rec["rows"]:
        row["sha"] = "0" * 40
    codes = _codes(rec, require_resolvable=True)
    # ONE V11, not five: the five rows share one pointer and V11 names the
    # POINTER (a note that said "5 row sha(s)" about one sha is a wrong
    # remediation target).
    assert codes.count("V11") == 1, codes
    assert "V5" not in codes, codes
    # and it is reported (not silently dropped) without the flag
    findings = cvc.check(rec, repo=_REPO, require_green=False)
    assert {kind for kind, _p, _w in findings.unresolved} == {"row"}


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


def test_a_measured_row_against_a_log_with_no_corpus_sha_is_a_finding():
    """REGRESSION (#9089 review): the compare used to be SKIPPED when the log
    carried no corpus sha (``and corpus.get("sha256")``), so a measured row
    naming ANY corpus passed against a log that names none.

    Non-vacuity: a GREEN row with ``corpus_sha256="b"*64`` against a log whose
    ``corpus.sha256`` is null returned no finding before the fix. "Two corpora
    cannot share one verdict" is unenforceable where nobody wrote down which
    corpus ran — so the absence IS the finding.
    """
    rec = _record()
    _green(rec)
    rec["corpus"] = {"path": "corpus.json", "sha256": None, "turns": None}
    rec["rows"][0]["corpus_sha256"] = "b" * 64
    assert "V7" in _codes(rec)


def test_a_measured_E4_with_the_4_edge_denominator_is_a_finding():
    """E4's row says it *fails if the denominator is 4* — the historical defect
    merged 4 of the 15 reachable edges, so a 4 is the defect re-appearing.

    The probe computed `denominator_is_the_4_edge_defect` and used it only in a
    NOTE, so a measured E4 with denominator 4 passed the gate (#9089 review).
    """
    rec = _record()
    _green(rec)
    e4 = rec["rows"][3]
    e4.update(verdict="GREEN", evidence_kind="measurement",
              evidence=rec["rows"][0]["evidence"], numerator=3, denominator=4,
              blocked_by=None, corpus_sha256="a" * 64)
    assert "V10" in _codes(rec)
    # E1 (denominator 50) on the same record is not the defect
    assert "V10" not in _codes(_record())


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


def test_two_verdict_blocks_is_a_usage_error_not_a_stale_green(tmp_path, capsys):
    """REGRESSION (#9089 review): the gate read only the FIRST block.

    `text.index(BEGIN)`/`text.index(END)` stop at the first marker pair, so a log
    holding a valid table followed by a NEWER run answered off the older one.
    Reachable with no forgery at all: piping a re-run into the committed log
    (`capstone_run.py | tee -a capstone.log`) is enough, and the gate is the only
    thing that could have noticed.

    The two blocks are the SHIPPED log's own machine table, duplicated — the
    `tee -a` shape exactly, and a VALID table (the single-copy control below is
    exit 0), not a hand-built summary fixture. MEASURED with the parent commit's
    `extract_block` swapped back in over this same file: exit 0, "structurally
    sound", because the first copy decided and the second was never read. Post-fix
    it is a usage error.
    """
    single = LOG.read_text()
    assert cvc.main([str(LOG), "--repo", str(_REPO)]) == 0  # the control
    doubled = single + "\n" + single[single.index(cvc.BEGIN):]
    log = tmp_path / "appended.log"
    log.write_text(doubled)
    assert cvc.main([str(log), "--repo", str(_REPO)]) == 2
    err = capsys.readouterr().err
    assert "exactly one verdict block" in err, err
    assert "2" in err and str(cvc.BEGIN) in err, err
    # And the module-level contract, so a direct caller cannot bypass main().
    import pytest
    with pytest.raises(ValueError, match="exactly one verdict block"):
        cvc.extract_block(doubled)


# ── the RUN's observers — every row is derived from these ────────────────
#
# TWO fixtures, deliberately, because there are two different objects and
# conflating them is what made the original observer tests vacuous (#9080
# review F2/P1). ``_extractor_result`` is ``extractor_v2.extract_session_v2``'s
# OWN return; ``_receipt`` is what ``TortoiseSDK.capture_session`` — the entry
# point the probe actually drives — hands back. The probe holds the SECOND one.
#
# #9116's seam is what reconciles them: ``_capture_step_witness`` copies the
# stages' own facts onto the receipt's ``stats`` key, so ``_seam_receipt`` below
# is the object a CURRENT ``capture_session`` returns — built by calling the real
# SDK helper, never by hand (a hand-built witness could not red when the SDK
# stops producing one, which is the whole point of pinning this).
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
        # The VET surface as ``_run_vet_pass`` really shapes it: its counts
        # under ``stats``, plus a ``decisions`` block that mirrors the embed
        # list's own text PER CANDIDATE (``vet_gate.vet_candidates``). The seam
        # must forward the first and never the second (#10214 review, P1) — the
        # text is here so a leak reds instead of passing on a thin fixture.
        "vet": {"enabled": True,
                "s2": {"stats": {"candidates": 3, "discarded": 0,
                                  "by_outcome": {"KEEP": 3},
                                  "arbiter": "none"},
                       "batch": {"size": 3},
                       "decisions": {"pt_0": {"section": "points",
                                               "index": 0,
                                               "text": "Ana Ruiz owns the "
                                                       "rollout of the "
                                                       "Postgres record",
                                               "outcome": "KEEP",
                                               "rule_id": None,
                                               "reason": None}}},
                "union": {}},
        "classify_later": {"enabled": True, "s2": {"assigned": 3}, "union": {}},
        "search": {"mode": "docker", "degraded": False, "reason": None,
                   "queries_run": 15, "entities": [{"id": "o_1"}],
                   "points": [], "events": []},
        "payload": {"points": []},
    }
    for k, v in over.items():
        if v is None:
            result.pop(k, None)
        else:
            result[k] = v
    return result


def _seam_receipt(**over) -> dict:
    """A CURRENT ``capture_session`` receipt — built by the real SDK seam.

    ``tortoise.sdk._capture_stats_with_step_witness`` is the production code that
    puts ``stats["step_witness"]`` on the receipt (#9116), so calling it here
    makes every assertion below a statement about shipped behaviour: let the SDK
    stop copying a stage's block and these tests red.
    """
    from tortoise.sdk import _capture_stats_with_step_witness

    out = _extractor_result(**over)
    return {
        "session_id": "capstone_abc123",
        "turns": 1,
        "extracted": 15,
        "points": [],
        "extraction_mode": "llm:deepseek-direct",
        "ok": True,
        "errors": [],
        "warnings": [],
        "diagnostics": [],
        "stats": _capture_stats_with_step_witness(out, 15),
        "capture_redactions": 0,
    }


def _receipt(**over) -> dict:
    """A ``capture_session`` receipt that carries NO ``step_witness``.

    The keys are the ``resp`` literal the SDK builds (minus the additive
    protocol keys, which merge flat and add no pipeline surface) with the
    ``stats`` sub-key the seam adds REMOVED — i.e. the receipt a REPLAY, a
    keyless capture or the M2 lane returns. A test that shapes this fixture
    differently from that literal is asserting a contract the caller can never
    satisfy — which is the vacuity this file was caught in.

    It is not "the receipt" any more: since #9116's seam a v2 receipt carries
    ``stats["step_witness"]``. Keeping this shape is what lets the tests below
    hold BOTH arms apart — a receipt with no seam must still witness the four
    steps ``stats`` carries and be refused for the four that need the seam.
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


def test_all_eight_steps_are_observable_on_the_object_the_probe_holds():
    """#9116, the acceptance: 8/8 through the REAL entry point's receipt.

    Both halves are the shipped code — ``tortoise.sdk._capture_stats_with_step
    _witness`` builds the ``stats`` the SDK puts on the receipt, and the probe's
    ``observe_steps`` reads it. Non-vacuity: at the pre-fix head this receipt
    carried no ``step_witness``, so the four steps that need it were absent and
    this equality reds.
    """
    result = _seam_receipt()
    assert "step_witness" in result["stats"], sorted(result["stats"])
    seen = probe.observe_steps(result)
    assert set(seen) == set(cvc.STEPS), sorted(set(cvc.STEPS) - set(seen))
    assert all(isinstance(v, str) and v for v in seen.values())


def test_a_receipt_with_no_seam_still_witnesses_the_four_stats_carry():
    """The other arm: no seam ⟹ four steps, and the gap NAMED as the instrument.

    A replay, a keyless capture and the M2 lane all return a receipt with no
    ``step_witness``. Those four are then unobservable, and that must be stated
    as the missing surface (``STEPS_NEEDING_WITNESS``) rather than as a pipeline
    that skipped VET/classify/search/commit.
    """
    seen = probe.observe_steps(_receipt())
    assert set(seen) == {"S0", "S1", "S2.1", "S5"}, sorted(seen)
    assert set(probe.STEPS_NEEDING_WITNESS) == set(cvc.STEPS) - set(seen)


def test_every_witness_block_observe_steps_reads_is_produced_by_the_seam():
    """The contract between the observer and the seam that feeds it (#9116).

    Derives BOTH sides from code, so neither can drift behind a comment:
      * the READ set — every ``w.get("X")`` in ``observe_steps`` — must equal
        ``probe.WITNESS_KEYS``;
      * the PRODUCED set — the top-level keys of
        ``tortoise.sdk._capture_step_witness``'s return, called on a real
        ``extract_session_v2``-shaped ``out`` — must cover every one of them.

    Mutations that red it: read an undeclared ``w.get("foo")``; let the SDK drop
    a block from the witness; rename a block on either side.
    """
    import ast

    from tortoise.sdk import _capture_step_witness

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
        if (isinstance(target, ast.Name) and target.id == "w"
                and isinstance(key, ast.Constant) and isinstance(key.value, str)):
            read.add(key.value)
    assert read == set(probe.WITNESS_KEYS), (
        sorted(read ^ set(probe.WITNESS_KEYS)), "observer read set != declared")

    produced = _capture_step_witness(_extractor_result(), committed=15)
    assert set(probe.WITNESS_KEYS) <= set(produced), (
        sorted(set(probe.WITNESS_KEYS) - set(produced)),
        "the observer reads a witness block the seam does not produce")
    # the dead branch the review found: neither side names a receipt key that
    # does not exist, so a "just copy the extractor's keys" fix cannot sneak in
    assert "extraction" not in produced
    # and the block that carries the one fact no fallback can supply
    assert "queries_run" in produced["search"]


def test_the_witness_forwards_the_vet_counts_and_never_its_candidate_text():
    """The seam's stated bound, held against the surface's real shape (#10214).

    ``_run_vet_pass`` returns ``{"stats", "batch", "decisions"}`` and
    ``decisions`` carries one row PER CANDIDATE with that candidate's own
    ``text`` (plus a ``counterfactual`` quoting it on a discard). The receipt is
    the object ``capture_session`` returns and the hosted response body, so a
    witness that forwards the surface grows with candidate count × text length —
    the exact content ``payload``/``embed_list`` were kept off it to avoid, and
    the opposite of the docstring's "counts and the stage's OWN small stats".

    Mutation that reds it: forward the surface (``"s2": vet.get("s2") or {}``),
    which is what the pre-fix helper did — the fixture's candidate text then
    appears inside ``stats["step_witness"]``.
    """
    import json

    from tortoise.sdk import _capture_step_witness

    out = _extractor_result()
    assert out["vet"]["s2"]["decisions"], "the fixture must carry the leak"
    witness = _capture_step_witness(out, committed=15)
    assert witness["vet"]["enabled"] is True
    # the COUNTS are forwarded — a witness still says the pass did work
    assert witness["vet"]["s2"]["candidates"] == 3, witness["vet"]
    assert "decisions" not in witness["vet"]["s2"], witness["vet"]
    rendered = json.dumps(witness)
    assert "Ana Ruiz owns the rollout" not in rendered, (
        "the witness carried extracted candidate text — a per-candidate surface "
        f"was forwarded instead of its counts: {witness['vet']}")
    # a projection of the surface, not merely "smaller today": the witness's
    # vet block is measured against the surface it projects
    assert len(json.dumps(witness["vet"])) < len(json.dumps(out["vet"])), (
        "the witness is not a projection of the Vet surface: "
        f"{len(json.dumps(witness['vet']))} >= {len(json.dumps(out['vet']))}")


def test_gate_counts_reads_the_bare_classify_shape_it_documents():
    """``_gate_counts`` must answer for BOTH gate shapes it names (#10214 review, P3).

    Two shapes reach this helper and they are not the same shape:

      * the VET surface — ``_run_vet_pass``' ``{"stats", "batch", "decisions"}``,
        counts NESTED under ``stats``;
      * a classify pass's stats — the BARE dict, with no ``stats`` key at all
        (``extractor_v2``: ``s2_classify_stats = out["stats"]``).

    The docstring named both and the code implemented one, so
    ``_gate_counts(<bare classify block>)`` answered ``{}``: a pass that DID work
    reported as IDLE — the misattribution the three-state G5 split exists to
    prevent, one layer below where it was fixed. No verdict shipped wrong only
    because ``_capture_step_witness`` forwards ``classify_later`` raw; the trap was
    armed for the next caller who read the docstring and routed a classify surface
    through this helper.

    Mutation that reds it: return ``{}`` when ``surface["stats"]`` is absent (the
    pre-fix body) — the bare block then reads as an idle pass.
    """
    from tortoise.sdk import _gate_counts

    # (1) the BARE classify shape: counts only, returned as they are.
    classify_block = {"items": 7, "assigned_knn": 5, "assigned_llm": 2,
                      "unassigned": 0}
    assert _gate_counts(classify_block) == classify_block
    assert _gate_counts(classify_block), (
        "a classify pass that assigned 7 kinds was reported as an IDLE pass — "
        "the bare shape is not read"
    )

    # (2) the NESTED VET shape: only its ``stats``, never the per-candidate block.
    vet_surface = {"stats": {"candidates": 3, "kept": 2},
                   "batch": {"prompt": "…"},
                   "decisions": [{"text": "candidate text", "verdict": "keep"}]}
    assert _gate_counts(vet_surface) == {"candidates": 3, "kept": 2}

    # (3) the bound holds in the BARE shape too: a surface that carries candidate
    # material is stripped even when it has no ``stats`` key, so the fallback can
    # never become the leak the nested branch was written to prevent.
    leaked = _gate_counts({"decisions": [{"text": "candidate text"}],
                           "batch": {"prompt": "…"}, "candidates": 3})
    assert leaked == {"candidates": 3}, leaked

    # (4) a non-dict is no pass at all.
    assert _gate_counts(None) == {}
    assert _gate_counts([]) == {}

def test_the_production_capture_path_puts_the_witness_on_the_receipt(monkeypatch):
    """#9116, the WIRING: the real ``capture_session`` returns the seam's record.

    Everything above builds the receipt by calling the helper directly, so none of
    it can tell whether ``_extract_session_v2`` — the production assembly —
    actually puts the helper's output on the receipt. This drives the real entry
    point over the deterministic mock extractor and reads the receipt it hands
    back.

    Mutation that reds it: revert the meta fold to ``"stats": out.get("stats")``
    (the pre-fix line) and the ``step_witness`` key disappears from a REAL
    capture.

    ⚠️ It asserts **7 of 8**, not 8: the mock's classify pass is ENABLED and does
    NO work (``{'enabled': True, 's2': {}, 'union': {}}``), so S2.3 is not
    witnessed — and must not be. Claiming it would be the "OFF population under an
    ON label" failure the bundle already paid for once (#9823). What S2.3 needs is
    a run whose classify pass assigns kinds, not a looser rule here.

    ⚠️ Two properties of the LANE it runs in are handled here rather than
    assumed, because this file is a `core` fast-surface member and NOT
    `uri_requiring:` (and that list is re-derived from COLLECTION aborts, so a
    test-level skip/failure is never subtracted from a URI-empty leg):

      * **the graph is `test`-prefixed** — `graph_name=` binds verbatim, and
        only `test_`/`tortoise_test_*` names are DETACH-DELETEd by the
        session-end sweep (`tests/_embedded.py::wipe_server`). A
        `capstone_…` name is outside every sweep, i.e. one stray graph per run
        on the shared instance (the #5084 class).
      * **S3 is asserted only where the backend can search** — over
        embedded/no URI the extractor's own `search_graph` short-circuits
        (`queries_run=0`, `degraded=True`), so S3 is unwitnessed by the
        BACKEND's answer, not by a seam defect. The wiring claim this test
        exists for does not need S3, so it is stated for the arm it is in
        rather than reding a leg that never promised a searchable backend.
    """
    import uuid as _uuid

    from tortoise.sdk import TortoiseSDK

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_VET", "1")
    monkeypatch.setenv("TORTOISE_CLASSIFY_LATER", "1")
    graph = f"test_capstone_9116_{_uuid.uuid4().hex[:10]}"
    sdk = TortoiseSDK(graph_name=graph)
    try:
        receipt = sdk.capture_session(
            [{"role": "user",
              "content": ("Daniel Ospina at Premise Labs decided on 2026-10-10 "
                          "to adopt the Postgres record as the system of "
                          "record; the rollout is owned by Ana Ruiz.")}],
            session_id=graph)
    finally:
        sdk.close()

    assert receipt.get("ok") is True, receipt.get("errors")
    stats = receipt.get("stats") or {}
    witness = stats.get("step_witness")
    assert isinstance(witness, dict) and witness, (
        "the production capture path does not put stats['step_witness'] on the "
        f"receipt — the #9116 seam is not wired into _extract_session_v2's meta "
        f"fold (stats keys: {sorted(stats)})")
    assert set(witness) == set(probe.WITNESS_KEYS), (
        sorted(set(probe.WITNESS_KEYS) ^ set(witness)))

    seen = probe.observe_steps(receipt)
    searchable, why = probe.backend_is_searchable()
    expected = set(cvc.STEPS) - {"S2.3"} - (set() if searchable else {"S3"})
    assert set(seen) == expected, (sorted(set(cvc.STEPS) - set(seen)), why)
    # S2.3's absence is the PASS's own answer, quoted from its block
    assert witness["classify_later"]["s2"] == {}, witness["classify_later"]
    assert witness["vet"]["s2"], witness["vet"]
    if searchable:
        assert witness["search"]["queries_run"], witness["search"]
    else:
        # the backend's own skip answer, NOT a seam defect — stated, not elided
        assert witness["search"]["queries_run"] == 0, (witness["search"], why)
    assert witness["commit"]["payload_produced"] is True, witness["commit"]


def test_a_vet_disabled_pass_does_not_observe_s2_2():
    """The flag-off arm runs no S2.2 — so its table cannot claim S2.2."""
    seen = probe.observe_steps(_seam_receipt(vet={"enabled": False, "s2": {}}))
    assert "S2.2" not in seen
    # and the other seven are still witnessed: one gated pass is not a reason
    # to lose the run's other evidence
    assert set(seen) == set(cvc.STEPS) - {"S2.2"}, sorted(seen)


def test_a_classify_disabled_pass_does_not_observe_s2_3():
    seen = probe.observe_steps(_seam_receipt(
        classify_later={"enabled": False, "s2": {}, "union": {}}))
    assert "S2.3" not in seen


def test_a_flag_on_pass_with_no_activity_is_not_s2_2():
    """`enabled: true` with empty counters is the flag, not the pass (#9823).

    The shape an idled (empty embed list) or fail-open pass leaves. Counting it
    would report an 8/8 from a run whose VET pass never ran — the "OFF
    population under an ON label" failure one layer up.
    """
    seen = probe.observe_steps(_seam_receipt(
        vet={"enabled": True, "s2": {}, "union": {}}))
    assert "S2.2" not in seen


def test_a_pass_without_a_payload_does_not_observe_s6():
    seen = probe.observe_steps(_seam_receipt(payload=None))
    assert "S6" not in seen


def test_a_degraded_search_is_not_s3():
    """A spent budget with `degraded=True` is a search that RAISED, not S3.

    `search_graph` increments `queries_run` BEFORE each attempt and, on the
    first-query exception, returns `{"degraded": True, "reason": "S3 degraded: 
    graph search failed (...)", "queries_run": q_run, ...}`
    (`tortoise/extractor_v2.py:2662-2692`) — so the pair (queries_run > 0,
    degraded=True) is exactly the shape of a search that failed. Scoring that
    attempt as resolved let V5, the epic's ONLY closing gate, pass on a step
    that never resolved, and every E-row measured on the run was then published
    as valid. `README.md:204` records the rule ("a degraded search is not S3")
    and the sibling probe already implements it
    (`capstone_measure.py::observe_steps`, the arm that writes that sentence into
    the step's own note).

    Non-vacuity: the healthy arm is asserted FIRST, so deleting the S3 witness
    outright cannot satisfy this pin.
    """
    assert "S3" in probe.observe_steps(_seam_receipt()), (
        "the healthy receipt must still witness S3, or the arm below proves "
        "nothing but an empty stage")
    degraded = probe.observe_steps(_seam_receipt(search={
        "mode": "docker", "degraded": True,
        "reason": "S3 degraded: graph search failed (timeout)",
        "queries_run": 1, "entities": [], "points": [], "events": []}))
    assert "S3" not in degraded, (
        "a degraded search was scored as S3 observed, so V5 can pass on a step "
        f"that never resolved: {degraded.get('S3')}")


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
    assert "INSTRUMENT gap" in reason, reason
    for step in probe.STEPS_NEEDING_WITNESS:
        assert step in reason, (step, reason)
    assert "NOT the pipeline" in reason, reason
    # and it says WHICH surface is missing, so the next lane does not have to
    # re-derive it: the receipt carries no step_witness block at all
    assert "step_witness" in reason, reason


def test_a_g5_refusal_on_a_seam_receipt_blames_the_pipeline_not_the_seam(
        tmp_path, monkeypatch):
    """The other arm (#9116): with the seam PRESENT, an absent step is the run's.

    A v2 receipt carries ``stats["step_witness"]``. If a step is then
    unobserved it is because that stage produced nothing or was gated off — a
    PIPELINE finding, quoted from the witness's own values. Conflating the two
    arms is what sent the epic's readers to the wrong issue in the first place.
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
            # VET gated off: the seam is here and says so
            return _seam_receipt(vet={"enabled": False, "s2": {}, "union": {}})

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)
    out = tmp_path / "g5-seam.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 5, code
    reason = cvc.extract_block(out.read_text())["run"]["reason"]
    assert "S2.2" in reason, reason
    # the PIPELINE arm, not the instrument one
    assert "INSTRUMENT gap" not in reason, reason
    assert "PIPELINE" in reason, reason
    # and the witness's own values are quoted, so the reader can act on it
    assert '"enabled": false' in reason, reason
    assert "step_witness" in reason, reason


def test_a_g5_refusal_names_an_incomplete_seam_as_the_seam(
        tmp_path, monkeypatch):
    """The THIRD state (#10214 review, P3): present seam, MISSING block.

    G5 split on "is there any ``step_witness``", so a seam that was present but
    carried no block for a step fell into the PIPELINE arm — the refusal then
    asserted the witness *"accounts for the missing step(s)"* and rendered
    ``S2.2: {}``, publishing a defect in ``_capture_step_witness`` as the
    extractor's own report. That is the misattribution the bundle's rule forbids;
    the sibling probe keeps the three states apart (``capstone_measure._seam_cause``
    names this one "present and INCOMPLETE, which is a defect in
    _capture_step_witness").

    Non-vacuity: on the pre-fix arm the reason says ``PIPELINE`` and quotes `{}`.
    """
    import tortoise.sdk as sdk_mod

    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    _read_prereg_ok(monkeypatch, _frozen_prereg(
        corpus={"sha256": probe.corpus_sha256(corpus), "items": 1}))
    monkeypatch.setattr(probe, "backend_is_searchable", lambda: (True, ""))
    monkeypatch.setattr(probe, "model_key_present", lambda: (True, ""))

    class _IncompleteSeamSDK:
        def capture_session(self, _turns, session_id=None):
            receipt = _seam_receipt()
            # the seam IS here — one of its blocks is not
            del receipt["stats"]["step_witness"]["vet"]
            return receipt

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _IncompleteSeamSDK)
    out = tmp_path / "g5-incomplete.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 5, code
    reason = cvc.extract_block(out.read_text())["run"]["reason"]
    assert "INCOMPLETE" in reason, reason
    assert "_capture_step_witness" in reason, reason
    assert "'vet' block" in reason, reason
    assert "PIPELINE" not in reason, reason
    # it must NOT claim the witness accounts for the step it does not carry
    assert "accounts for the missing" not in reason, reason


def test_a_degraded_search_refuses_the_completed_table(tmp_path, monkeypatch):
    """END TO END (#10214 review, P1): a degraded search cannot emit a table.

    `queries_run > 0` with `degraded=True` reaches G5 exactly like a resolved
    search did, and G2 cannot catch it (its `backend_is_searchable` is
    env-derived and issues no query). Before this pin the run took the G6 branch
    and wrote `status="COMPLETED"`, `reason="eight steps observed in one pass"`
    — a capstone table published from a step that raised.
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
            return _seam_receipt(search={
                "mode": "docker", "degraded": True,
                "reason": "S3 degraded: graph search failed (timeout)",
                "queries_run": 1, "entities": [], "points": [],
                "events": []})

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)
    out = tmp_path / "g5-degraded.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    record = cvc.extract_block(out.read_text())
    assert code == 5, (code, record["run"])
    assert record["run"]["status"] != "COMPLETED", record["run"]
    assert "S3" in record["run"]["reason"], record["run"]["reason"]


def test_a_completed_run_is_reachable_now_that_the_seam_landed(
        tmp_path, monkeypatch):
    """#9116's acceptance, end to end: G5 passes and G6 emits a table.

    Before the seam this was unreachable — the run refused at G5 for EVERY
    input, so no row could ever be GREEN and G6 was dead code. The pre-fix
    version of this test pinned that unreachability; this one pins the other
    side, so the gate cannot silently regress to refusing every run.
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
            return _seam_receipt()

    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _FakeSDK)
    out = tmp_path / "g6.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 0, (code, out.read_text()[-800:])
    record = cvc.extract_block(out.read_text())
    assert record["run"]["status"] == "COMPLETED", record["run"]
    assert record["run"]["steps_observed"] == list(cvc.STEPS), record["run"]
    assert not record["run"].get("steps_unobserved"), record["run"]
    assert {row["evidence_kind"] for row in record["rows"]} == {"measurement"}
    # every step a table is built from was OBSERVED in this pass
    assert set(record["run"]["steps_required"]) == set(cvc.STEPS)


def test_a_completed_run_does_not_blame_the_preregistration_it_just_read():
    """REGRESSION (#9089 review): G6 is reached only after G0 READ the frozen
    pre-registration, yet its rows said `evidence_kind="refusal"` and E1/E2
    named `#9075` as the blocker — telling the next lane that the
    pre-registration is still missing and calling a measurement a refusal.

    G6 itself is unreachable while #9116 leaves the receipt witnessless, which
    is why the row construction is a pure function: a fix that lives only
    inside dead code is a fix nothing can pin.
    """
    rows = probe.completed_rows(
        head="a" * 40, corpus_sha="b" * 64,
        receipt=probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                               "kept_from_s2": 14}}))
    assert [r["criterion"] for r in rows] == ["E1", "E2", "E3", "E4", "E5"]
    assert {r["evidence_kind"] for r in rows} == {"measurement"}, rows
    by_id = {r["criterion"]: r for r in rows}
    assert by_id["E1"]["blocked_by"] == probe.ALSO_BLOCKED_BY["E1"]
    assert by_id["E2"]["blocked_by"] == probe.ALSO_BLOCKED_BY["E2"]
    assert all(r["blocked_by"] != "#9075" or r["criterion"] not in ("E1", "E2")
               for r in rows)
    assert {r["sha"] for r in rows} == {"a" * 40}
    assert {r["corpus_sha256"] for r in rows} == {"b" * 64}
    # The sibling link stays (E1/E2 share one corpus) and E4 carries its receipt
    # in BOTH the row's numbers and its note.
    assert by_id["E1"]["sibling"] == "E2" and by_id["E2"]["sibling"] == "E1"
    assert "14/15" in by_id["E4"]["note"], by_id["E4"]["note"]
    assert (by_id["E4"]["numerator"], by_id["E4"]["denominator"]) == (14, 15)


def test_e4_receipt_flags_the_four_denominator():
    """E4's row fails when the denominator is 4 — the historical defect.

    ⛔ The flag is named for the NUMBER (`denominator_is_4`), not for the
    cause. E4's row is phrased over the 15-edge operator gold, but this surface
    measures the S4 merge's ITEM accounting; a name claiming "the 4-edge defect"
    would assert a quantity the receipt cannot see (#9089 review).
    """
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 4, "kept_from_s2": 4}})
    assert receipt["denominator_is_4"] is True
    assert "denominator_is_the_4_edge_defect" not in receipt, (
        "the flag must not claim a quantity (edges) this surface cannot measure")
    ok = probe.e4_receipt({"s4_merge": {"s2_items": 15, "kept_from_s2": 14}})
    assert ok["denominator_is_4"] is False
    assert (ok["numerator"], ok["denominator"]) == (14, 15)


def _completed_record(receipt: dict) -> dict:
    """The table a COMPLETED run emits, as the gate receives it."""
    rows = probe.completed_rows(head="a" * 40, corpus_sha="b" * 64,
                                receipt=receipt)
    return {
        "schema": cvc.SCHEMA, "issue": 9080, "epic": 7949, "head": "a" * 40,
        "prereg": {"sha": "a" * 40, "status": "OK", "artifact": "x"},
        "corpus": {"path": "corpus.json", "sha256": "b" * 64, "turns": 3},
        "run": {"status": "COMPLETED", "reason": "eight steps observed",
                "steps_observed": list(cvc.STEPS),
                "steps_required": list(cvc.STEPS), "e4_receipt": receipt},
        "rows": rows,
    }


def test_a_completed_run_carries_E4s_receipt_numbers_into_the_row():
    """REGRESSION (#9089 review, P2): E4's row carried `None`/`None` while the
    receipt that decides E4 sat in `run.e4_receipt` and in the row's NOTE.

    V10 fires on the ROW's denominator, so on the only table this probe can
    produce (verdict BLOCKED, evidence owned by another lane) the criterion's
    own failing condition was unreachable. Non-vacuity: before the fix the two
    asserts below read `None` — the numbers existed in the same log, unread.
    """
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                           "kept_from_s2": 14}})
    by_id = {r["criterion"]: r for r in
             probe.completed_rows(head="a" * 40, corpus_sha="b" * 64,
                                  receipt=receipt)}
    assert (by_id["E4"]["numerator"], by_id["E4"]["denominator"]) == (14, 15)
    # The other four criteria have no receipt on this surface — a fabricated
    # zero would read as a measurement.
    assert all(r["numerator"] is None and r["denominator"] is None
               for cid, r in by_id.items() if cid != "E4")


def test_a_completed_E4_with_the_four_denominator_reds_the_gate():
    """REGRESSION (#9089 review, P2): the gate must refuse E4 on the table the
    PROBE produces, not only on a hand-written GREEN row.

    V10 required `verdict in (GREEN, RED)`, and every row of a completed run is
    `BLOCKED` (E4's evidence artifact belongs to #9077) — so the rule was
    unreachable on the shipped producer's own output. Non-vacuity: measured
    before the fix, this record returned rc=0 "structurally sound" with
    `denominator_is_4: true` sitting unread in the same log.
    """
    four = probe.e4_receipt({"s4_merge": {"s2_items": 4, "kept_from_s2": 4}})
    assert "V10" in _codes(_completed_record(four))
    # A legitimately larger merge is not the defect.
    ok = probe.e4_receipt({"s4_merge": {"s2_items": 15, "kept_from_s2": 14}})
    assert "V10" not in _codes(_completed_record(ok))


def test_a_row_and_its_e4_receipt_that_disagree_is_a_finding():
    """E4 is decided by ONE number: the row and the run's receipt are two
    copies of it, so a hand-edit of either must not pick the verdict."""
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                           "kept_from_s2": 14}})
    rec = _completed_record(receipt)
    e4 = next(r for r in rec["rows"] if r["criterion"] == "E4")
    e4["denominator"] = 50          # the row edited away from the receipt
    assert "V10" in _codes(rec)


def test_a_measured_E4_with_an_empty_denominator_is_a_finding():
    """REGRESSION (#9089 review): V5's "an empty denominator measures
    nothing" rule lives in its GREEN arm, and E4 is never GREEN on this surface
    (`completed_rows` writes BLOCKED for every row while #9116 holds) — so 0/0
    was the one vacuous measurement the gate called sound.

    A run whose `s4_merge` is absent supplies no `s2_items`, and the receipt
    computes the denominator from it, so this shape is what the PROBE emits for
    a completed run with no merge step — not a hand-written table.

    Non-vacuity: measured on the pre-fix tool, `_codes()` came back empty for
    both shapes below and the tally printed "denominators agree".
    """
    empty = probe.e4_receipt({"s4_merge": {}})
    assert empty["denominator"] == 0, empty
    assert "V10" in _codes(_completed_record(empty))
    # And explicitly zero, the shape a fabricated row would carry.
    receipt = probe.e4_receipt({"s4_merge": {"s2_items": 15,
                                           "kept_from_s2": 14}})
    rec = _completed_record(receipt)
    e4 = next(r for r in rec["rows"] if r["criterion"] == "E4")
    e4["denominator"] = 0
    rec["run"]["e4_receipt"]["denominator"] = 0
    assert "V10" in _codes(rec)


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


def test_a_post_g0_refusal_does_not_blame_the_preregistration():
    """REGRESSION (#9089 review, cycle 2): `refused_rows` named `#9075` for
    E1/E2 even on a `prereg_ok=True` refusal — a table that records
    `prereg.status: OK` and, in the same breath, sends the next lane to the
    pre-registration issue it just read. The G6 half of this was fixed; this is
    the half on the REACHABLE path.
    """
    rows = probe.refused_rows("corpus missing", prereg_ok=True)
    by_id = {r["criterion"]: r for r in rows}
    assert by_id["E1"]["blocked_by"] == probe.ALSO_BLOCKED_BY["E1"]
    assert by_id["E2"]["blocked_by"] == probe.ALSO_BLOCKED_BY["E2"]
    assert by_id["E1"]["verdict"] != "UNPREREGISTERED"
    # The G0 refusal still says the prereg is the missing thing.
    g0 = {r["criterion"]: r for r in probe.refused_rows("no prereg",
                                                        prereg_ok=False)}
    assert g0["E1"]["verdict"] == "UNPREREGISTERED"
    assert g0["E1"]["blocked_by"] == probe.OWNERS["E1"]


def test_a_pre_g0_refusal_claims_nothing_about_the_preregistration(tmp_path):
    """A gate that stopped at G-1 never looked at the pre-registration.

    It must not write `prereg.status: ABSENT` (a claim about a check nobody ran)
    nor `UNPREREGISTERED` on E1/E2 (a claim that the prereg is what is
    missing). Measured on `--head not-a-sha`: the pre-fix table asserted both.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "g-1.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out), "--head", "not-a-sha"])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert record["prereg"]["status"] == "UNREAD", record["prereg"]
    assert {r["verdict"] for r in record["rows"]} == {"BLOCKED"}
    assert "UNPREREGISTERED" not in {r["verdict"] for r in record["rows"]}
    # and the refusal table is still clean for the gate that reads it
    assert "V3" not in _codes(record), _codes(record)


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
    not "it fails" — it is "it fails AND the table it leaves behind says what the
    checker needs to read": a refusal that produced an unparseable log would let
    the next lane mistake silence for a verdict.

    ⛔ THE CITATION IS THE P1 (#9089 review): every row used to carry a hard-coded
    ``EVIDENCE_DIR/capstone.log`` whatever ``--out`` said, so a run written to
    ``capstone-<sha>.log`` produced a table whose rows pointed at a DIFFERENT file
    that existed for unrelated reasons — the citation proved nothing. The rows now
    cite the artifact this run actually wrote, and here that artifact is OUTSIDE
    the repo (``tmp_path``), so the gate's V4 refuses the citation with the honest
    reason. That is the correct verdict, not a regression: an artifact the repo
    does not carry cannot be reviewed later. V4 is the ONLY finding — the table is
    otherwise sound — and ``--require-green`` still answers "not closeable".
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
    # the P1: the rows cite THIS run's artifact
    assert {row["evidence"] for row in record["rows"]} == {str(out)}
    findings = cvc.check(record, repo=_REPO, require_green=False)
    codes = sorted({code for code, _w, _m in findings.items})
    assert codes == ["V4"], findings.render()
    assert "V9" in _codes(record, require_green=True)


def test_a_row_cites_the_output_path_it_was_actually_written_to(tmp_path):
    """P1 pin on the PURE half: no path is hard-coded anywhere in the rows.

    ``evidence_path`` is the one place the citation is decided. Repo-relative
    stays repo-relative (so the gate can resolve it), and the no-``--out`` case
    falls back to the acceptance command's own ``tee`` target — the single case
    where that path IS the artifact.
    """
    import os

    assert probe.evidence_path(None) == str(probe.EVIDENCE_DIR / "capstone.log")
    relative = probe.EVIDENCE_DIR / "capstone-measured-test.log"
    assert probe.evidence_path(relative) == str(relative)
    rows = probe.refused_rows("x", prereg_ok=True, evidence=str(relative))
    assert {row["evidence"] for row in rows} == {str(relative)}
    completed = probe.completed_rows(
        head=HEAD, corpus_sha="c" * 64,
        receipt={"numerator": 1, "denominator": 15, "denominator_is_4": False},
        evidence=str(relative))
    assert {row["evidence"] for row in completed} == {str(relative)}
    # and the default (no --out) is the shipped path, which is what the
    # acceptance command's `| tee` writes
    default_rows = probe.refused_rows("x", prereg_ok=True)
    assert {row["evidence"] for row in default_rows} == {
        os.path.join(str(probe.EVIDENCE_DIR), "capstone.log")}


def _frozen_prereg(**over) -> dict:
    record = {"schema": probe.PREREG_SCHEMA, "written_before_any_run": True}
    record.update(over)
    return record


def _read_prereg_ok(monkeypatch, record: dict | None = None) -> None:
    """G0 PASSES, so the run reaches G1+ — the half no test used to reach."""
    fixed = record if record is not None else _frozen_prereg()
    # The reader takes the commit the rows are STAMPED with (``head``), not the
    # worktree's: G0's ancestry test resolves against what was measured.
    monkeypatch.setattr(probe, "read_preregistration",
                        lambda _sha, _artifact, _head=None: (fixed, ""))


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


def test_the_readmes_line_citations_resolve_to_the_symbols_they_name():
    """A cite that says it was re-derived at the head must BE the head's line.

    #9089 review finding 2: the README printed
    `` `sdk._extract_session_v2` (`sdk.py:10341`; called at `:9358`) `` in the
    same paragraph that claimed the numbers were "re-derived at the head" — the
    def is at 10422 and the call site at 9439 at this head, so BOTH were 22
    lines stale (they named a comment inside the function and an unrelated
    line), carried from an earlier revision and repeated in the PR body. The
    prose cannot be the only thing keeping a cite true; it is mechanical, so it
    is pinned. `main` moved `sdk.py` under the cites again while this branch
    was open, which is exactly the drift this test exists to catch.

    Non-vacuity: at the pre-fix head `sdk.py:10341` is a comment line inside
    `_extract_session_v2`, so the `startswith("def …")` assertion reds, and
    `:9358` carries no call to it, so the call-site assertion reds too.
    """
    readme = (PROBE.parents[1] / "README.md").read_text()
    sources = {
        "sdk": (_REPO / "tortoise/sdk.py").read_text().splitlines(),
        "extractor_v2": (_REPO / "tortoise/extractor_v2.py").read_text().splitlines(),
    }

    # (a) The entry-point cites: the def, the CALL site and the one-pass body.
    sdk_cites = re.search(r"`sdk\.py:(\d+)`; called at `:(\d+)`", readme)
    assert sdk_cites, "the sdk.py cite paragraph moved; re-derive it, do not drop the pin"
    def_line, call_line = (int(n) for n in sdk_cites.groups())
    assert sources["sdk"][def_line - 1].lstrip().startswith("def _extract_session_v2("), (
        f"README cites the _extract_session_v2 def at sdk.py:{def_line}, which is "
        f"{sources['sdk'][def_line - 1].strip()!r} — the line moved and the cite did not")
    assert "self._extract_session_v2(" in sources["sdk"][call_line - 1], (
        f"README cites the call at sdk.py:{call_line}, which is "
        f"{sources['sdk'][call_line - 1].strip()!r} — the line moved and the cite did not")
    # Anchored on the entry-point SENTENCE, not on the first backticked
    # `extractor_v2.py:NNNN` anywhere in a 40 KB document: the bundle cites
    # dozens of lines in that file (the flag reads, the gates, the surfaces),
    # so "the first one" is whichever paragraph happens to come first — it
    # pinned nothing about the entry point and red-ed the moment a cite was
    # added above it.
    entry = re.search(
        r"`extractor_v2\.extract_session_v2` \(`extractor_v2\.py:(\d+)`\)",
        readme)
    assert entry, "the extractor_v2 entry-point cite is gone"
    entry_line = int(entry.group(1))
    assert sources["extractor_v2"][entry_line - 1].lstrip().startswith(
        "def extract_session_v2("), (
    f"README cites extract_session_v2 at extractor_v2.py:{entry_line}, which is "
    f"{sources['extractor_v2'][entry_line - 1].strip()!r}")

    # (b) The eight-row step table: `` `symbol` `:NNNN` `` into the same file.
    pairs = re.findall(r"`([A-Za-z_][\w.]*)`\s*`:(\d+)`", readme)
    assert len(pairs) >= 8, f"the step table's cites are gone: {pairs}"
    for symbol, lineno in pairs:
        text = sources["extractor_v2"][int(lineno) - 1]
        assert symbol.split(".")[-1] in text, (
            f"README cites `{symbol}` at extractor_v2.py:{lineno}, which reads "
            f"{text.strip()!r} — the line moved and the cite did not")
    # (c) The probe's nine bare step cites (#10211). They name a SYMBOL and a
    # bare `:NNNN` with no filename, so rule (b) cannot see them (it reads the
    # README) and the bundle's own sweep cannot either (`_BUNDLE_CITE` requires
    # `file.py:line`). Nothing watched them: `#10243` moved `extractor_v2.py` by
    # +88 and every cite went stale while both pin files stayed green. Same rule
    # as (b), applied to the file that carries them.
    #
    # #10214 review (blocking): this sweep accepted the cite if EITHER symbol of
    # a `a`/`b` PAIR appeared on the line (`any`), so the S2.3 pair
    # (`classify_items`/`_apply_classify_kinds` at `:8700`) passed here on
    # `classify_items` alone while `_apply_classify_kinds` sat on `:8701`. A pair
    # cite claims BOTH symbols are on that line, so the test for it is `all` —
    # and the probe now carries one cite per symbol anyway, which is the shape
    # the README's step table already had. Non-vacuity: restoring the pair at
    # `:8700` reds both this rule (all → False on `_apply_classify_kinds`) and
    # the population assert below (8 vs 9).
    probe = re.sub(r"`{2}", "`", PROBE.read_text())
    step_pairs = re.findall(
        r"`([A-Za-z_][\w.]*)`(?:\s*/\s*`([A-Za-z_][\w.]*)`)?"
        r"\s*\((?:S[\d.]+(?:\+S\d+)?|the gap filler),[^`]*`:(\d+)`",
        probe)
    assert len(step_pairs) >= 9, (
        f"the probe's step list is gone or was reformatted: {step_pairs}")
    for first, second, lineno in step_pairs:
        text = sources["extractor_v2"][int(lineno) - 1]
        named = [s.split(".")[-1] for s in (first, second) if s]
        assert all(sym in text for sym in named), (
            f"the probe cites `{first}`"
            + (f" / `{second}`" if second else "")
            + f" at extractor_v2.py:{lineno}, which reads {text.strip()!r} — "
            "the line moved and the cite did not")
    # (d) The probe's ENTRY-POINT sentence, the same (def, call) pair rule (a)
    # pins in the README (#10021 review). Rule (c) covers only the probe's nine
    # bare STEP cites, so this sentence — the one that names the entry point a
    # reader reproduces from — was the ONLY cite in the bundle that nothing
    # re-checked. It rotted exactly as rule (a)'s README twin did: the
    # re-derivation moved the `def` half and left the `called at` half
    # untouched, so `capstone_run.py:19` and `README.md:615` stated two
    # different, both-wrong call sites for one fact, and both pin files stayed
    # green. Two documents stating one fact is a claim that can only be pinned
    # where each one states it. (The numbers are deliberately NOT quoted here:
    # this paragraph outlived two heads, and a quoted line number is the very
    # drift it describes — this paragraph's own earlier revision quoted both
    # halves, and the merge that brought `origin/main` in moved them.)
    #
    # Non-vacuity: at the pre-fix head the probe named the def and left the
    # call site at a line carrying NO call to `_extract_session_v2` — measured on
    # CI, not reasoned: `tests/test_7949_capstone_verdict_check.py::
    # test_the_probes_line_citations_resolve_too` was the dispatched red, with
    # `assert 'self._extract_session_v2(' in <that line>` failing. So the
    # call-site assertion reds on the stale cite. It is the assertion that was
    # already red in the README before this round; the pin now covers the second
    # site too.
    probe_entry = re.search(r"`sdk\.py:(\d+)`, called at `:(\d+)`", probe)
    assert probe_entry, (
        "the probe's entry-point cite moved or was reformatted; re-derive it, "
        "do not drop the pin")
    p_def, p_call = (int(n) for n in probe_entry.groups())
    assert sources["sdk"][p_def - 1].lstrip().startswith("def _extract_session_v2("), (
        f"the probe cites the _extract_session_v2 def at sdk.py:{p_def}, which "
        f"is {sources['sdk'][p_def - 1].strip()!r} — the line moved and the "
        "cite did not")
    assert "self._extract_session_v2(" in sources["sdk"][p_call - 1], (
        f"the probe cites the call at sdk.py:{p_call}, which is "
        f"{sources['sdk'][p_call - 1].strip()!r} — the line moved and the cite "
        "did not")


def test_the_readme_stamps_the_shipped_log_at_the_logs_own_head():
    """ONE head for the shipped measurement (#9089 review finding 3).

    The bundle printed a different sha for the same measurement in two places
    (`Measured at: 6ccc06a23` here, `Re-measured at 717355e67` in the re-read
    section and the probe's docstring), and the stamp itself named a commit
    that did not exist anywhere in this repo — so the Reproduce block could not
    regenerate the shipped log at its own stamp and no reader could check the
    claim. The re-read base is a statement-level re-read of the ISSUE BODY, not
    a second measurement head, and it is labelled that way now.

    Non-vacuity: the pre-fix README and probe docstring both carried
    `Re-measured at `717355e67``, so the second assertion reds there. The first
    would not (the two shas agreed) — the drift it catches is mechanical, and
    it reds the moment either the README or a re-run of the probe moves without
    the other.
    """
    readme = (PROBE.parents[1] / "README.md").read_text()
    log_head = re.search(r"^# head: ([0-9a-f]{40})$", LOG.read_text(),
                         re.MULTILINE)
    assert log_head, "the shipped log carries no `# head:` stamp"
    cited = re.search(r"\*\*Measured at:\*\* `([0-9a-f]{7,40})`", readme)
    assert cited, "the README carries no `Measured at` stamp"
    assert log_head.group(1).startswith(cited.group(1)), (
        f"the README stamps the bundle at {cited.group(1)}; the log it ships is "
        f"stamped {log_head.group(1)} — one measurement, one head")
    for name, text in (("README.md", readme),
                       ("probes/capstone_run.py", PROBE.read_text())):
        assert "Re-measured at" not in text, (
            f"{name} prints a SECOND head for the capstone measurement; the "
            "issue-body re-read is not the log's stamp")


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
    # The COMMENT-ONLY tail, exactly as the refresher reads it (`tools/ci_timing.py`
    # matches `^[ \t]*#\s*unmeasured\b` against the tail it CLEARS on refresh).
    # ⛔ Slicing from the first `:` instead (`row.split(':', 1)[1]`) starts at the
    # WEIGHT — ` 2.0  # unmeasured …` — so the marker could never match and the
    # tolerance below was unreachable: measured, the first post-merge sweep CLEARS
    # the tail, leaves `test_…py: 4.2`, and this test then redded `main` — the
    # exact false red the tolerance exists to prevent.
    tail = row.split("#", 1)[1] if "#" in row else ""
    unmeasured = bool(re.match(r"[ \t]*unmeasured\b", tail))
    # A sweep CUTS the file on its first measurement, so on that pass the whole
    # note is cleared away and the row is left as a bare `key: value` (measured:
    # `tools/ci_timing.py::render_refreshed_manifest` blanks the tail when it
    # LEADS with `# unmeasured`). A bare row and a still-unmeasured row are both
    # the refresher's state — the count is required only of a row that carries a
    # note this repo wrote and the sweep has not yet taken over.
    refreshed_or_unmeasured = unmeasured or not tail.strip()
    quoted = re.search(r"(\d+)\s+tests", row)
    if quoted is None:
        assert refreshed_or_unmeasured, (
            "a hand-written durations note must quote the measured test count, "
            f"or LEAD with `# unmeasured` (the refresher owns that tail): {row}")
        return
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
    # The refusal IS a fact measured at THIS checkout, so it carries the
    # worktree's HEAD — not the string "unknown". A refusal table is piped over
    # the committed evidence file by the acceptance command (`| tee`), so a
    # stamp `git cat-file` cannot resolve would replace the shipped log with a
    # table this repo's own gate (V3) reds.
    assert record["head"] == HEAD, record["head"]
    assert {row["sha"] for row in record["rows"]} == {HEAD}
    # ⛔ V4 IS EXPECTED HERE, and only here (#9089 review P1). Every row now cites
    # the artifact THIS run wrote, and `--out` is a tmp path outside the repo — so
    # the gate refuses the citation with the honest reason (an artifact the repo
    # does not carry cannot be reviewed later). The provenance rules this test
    # guards are V3 (sha SHAPE) and V6 (a named blocker): those must stay clean.
    assert {row["evidence"] for row in record["rows"]} == {str(out)}
    codes = _codes(record)
    assert not [c for c in codes if c in ("V3", "V6")], codes


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
    assert record["head"] == HEAD, record["head"]
    assert "V3" not in _codes(record), _codes(record)


def test_a_head_override_naming_a_different_tree_is_refused(tmp_path, monkeypatch):
    """#9089 review P2: the override must name the TREE this process executed.

    `--head` checked that the string was a sha and that it RESOLVED — not that the
    code that ran is that revision. Measured on the pre-fix probe: `--head HEAD~5`
    from an unmodified checkout stamped every row `HEAD~5` while executing `HEAD`,
    so the evidence bundle recorded "measured at X" for code that never ran at X
    (and V5 then resolves the pre-registration's ancestry against that false
    stamp). The honest test is the tree: a tag, or the same tree checked out
    elsewhere, has the same `^{tree}` and stays legitimate; a different tree is a
    different program.

    Non-vacuity: the pre-fix `resolve_head` returns the override unchanged for BOTH
    commits below, so the first assertion reds (no error is produced).
    """
    repo = tmp_path / "tree_repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "r@example.com")
    git("config", "user.name", "R")
    (repo / "code.txt").write_text("v1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    old = git("rev-parse", "HEAD")
    (repo / "code.txt").write_text("v2\n")
    git("commit", "-q", "-am", "two")
    same_tree = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "three")
    monkeypatch.setattr(probe, "ROOT", repo)

    head, err = probe.resolve_head(old)
    assert err and "DIFFERENT tree" in err, (head, err)
    assert head == git("rev-parse", "HEAD"), head

    # the same TREE at a different commit is the documented legitimate case
    head, err = probe.resolve_head(same_tree)
    assert err == "" and head == same_tree, (head, err)


def test_a_checkout_with_no_resolvable_head_is_refused(tmp_path, monkeypatch):
    """#9089 review P2: an unborn HEAD has no commit to stamp, and `HEAD` is not one.

    `git rev-parse HEAD` in a fresh `git init` prints the literal string `HEAD`
    and exits 128, so `.stdout.strip() or "unknown"` yielded `HEAD` — and
    `emit_log` then stamped the table's `head` and EVERY row's `sha` with it. The
    emitted table is red by this repo's own V3 rule, and the documented
    `| tee docs/evidence/7949-capstone/capstone.log` would replace the shipped
    evidence with an artifact the repo reds. The docstring promises the string
    `"unknown"` never reaches a stamp without an error; this pins it.
    """
    repo = tmp_path / "unborn"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True)
    monkeypatch.setattr(probe, "ROOT", repo)
    head, err = probe.resolve_head(None)
    assert err, (head, err)
    assert head == "unknown", head
    assert not probe.HEAD_SHA_RE.match(head), head


def test_a_deeply_nested_verdict_block_is_a_usage_error(tmp_path):
    """#9089 review P2: `RecursionError` is not a `JSONDecodeError`, and it must not be a verdict.

    A block nested past the parser's limit used to escape `extract_block`'s ValueError
    contract entirely and surface as a traceback with exit 1 — the code this tool
    reserves for "the table has FINDINGS" — on a document that was never read. The
    arm that converts it is only half the fix if nothing pins it: delete it and the
    traceback comes back silently.
    """
    log = tmp_path / "deep.log"
    log.write_text(f"{cvc.BEGIN}\n" + "[" * 100000 + f"\n{cvc.END}\n")
    assert cvc.main([str(log)]) == 2


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


def test_prereg_ancestry_is_resolved_against_the_measured_head(
        tmp_path, monkeypatch):
    """REGRESSION (#9089 review): G0 resolved ancestry against ``rev-parse
    HEAD``, never against the commit the rows are stamped with.

    With ``--head <older commit>`` the two differ, and the old form then
    accepted a pre-registration committed AFTER the measured commit — emitting
    a table whose every row is stamped with a pre-threshold commit, exactly the
    state V5's "evidence PREDATES the pre-registration" rule refuses.

    Non-vacuity: the pre-fix reader returns the record (G0 PASSES) for this
    pair; the assertion below reds on it.
    """
    repo, parent, _child = _ancestry_pair(tmp_path)
    (repo / "docs/evidence/7949-capstone/preregistration.json").write_text(
        json.dumps(_frozen_prereg()))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "prereg"],
                   check=True)
    frozen = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True,
                            check=True).stdout.strip()
    # `_git` shells out against `ROOT`; monkeypatch restores it for the rest of
    # the suite (a bare assignment here poisoned every later test's git calls).
    monkeypatch.setattr(probe, "ROOT", repo)
    record, why = probe.read_preregistration(
        frozen, Path("docs/evidence/7949-capstone/preregistration.json"),
        parent)
    assert record is None, ("a pre-registration committed AFTER the measured "
                            "head must not pass G0")
    assert "not an ancestor" in why, why


def test_read_preregistration_refuses_a_commit_without_the_artifact(
        tmp_path, monkeypatch):
    """The shipped log's state at its own stamp: the artifact was ABSENT (#9075).

    ⚠️ This arm used to point at THIS checkout's ``HEAD``, because that WAS the
    shipped state when it was written. `#9075` has since landed its record
    (`#9507`), so `HEAD` carries it and the old form now asserts the opposite of
    the tree — a false red. The refusal is pinned against a commit that
    genuinely lacks the artifact, which is the state it exists for; the LANDED
    record gets its own pin below (`…is_read_not_refused_for_its_claim_name`).

    Non-vacuity: the artifact is absent from that commit, so the pre-fix reader
    returns this same refusal — the arm pins the MESSAGE (the reason a lane
    reads) and the landing arm reds on the pre-fix key requirement.
    """
    repo, _parent, child = _ancestry_pair(tmp_path)
    # `_git` shells out against `ROOT`; monkeypatch restores it for the suite.
    monkeypatch.setattr(probe, "ROOT", repo)
    record, why = probe.read_preregistration(
        child, Path("docs/evidence/7949-capstone/preregistration.json"))
    assert record is None
    assert "does not carry" in why
    assert "#9075" in why


def test_the_landed_preregistration_is_read_not_refused_for_its_claim_name():
    """#9075 LANDED (#9507) — G0 must READ its record, not refuse its spelling.

    The landed record asserts ``written_before_any_scored_run`` **with its
    basis** (`PRE-REGISTRATION.md` check 3 requires exactly that, and refuses
    the unqualified ``written_before_any_run`` because it is false as soon as
    any log exists), while this probe demanded only the unqualified name. So the
    only legitimate pre-registration in the repo was refused at G0 and the
    instrument could never read one at all — an inert gate standing in front of
    the epic's closing rule.

    Non-vacuity: the pre-fix reader returns a refusal ("does not assert
    written_before_any_run") for this exact file at this head.
    """
    artifact = Path("docs/evidence/7949-capstone/preregistration.json")
    assert (_REPO / artifact).is_file(), (
        "the pre-registration is committed on `main` (#9507); if it is gone, "
        "the bundle's dependency claim changed and this pin must be re-derived")
    record, why = probe.read_preregistration(HEAD, artifact)
    assert record is not None, why
    assert record["schema"] == probe.PREREG_SCHEMA
    assert record["written_before_any_scored_run"] is True


def test_a_scored_run_claim_without_its_basis_is_refused(tmp_path, monkeypatch):
    """The landed claim form is only checkable against the reason it gives.

    ``written_before_any_scored_run: true`` with no basis is the bare boolean
    `PRE-REGISTRATION.md` check 3 rejects, so accepting the new spelling must not
    become a fail-open: the basis is part of the claim.

    Non-vacuity: the pre-fix reader refuses this record too, but for the WRONG
    reason (it never looks at the scored-run claim), and
    ``test_the_landed_preregistration_is_read_not_refused_for_its_claim_name``
    is what reds on the pre-fix code. This arm keeps the new arm honest.
    """
    for missing, fragment in ((None, "without stating its basis"),
                              ("   ", "without stating its basis")):
        payload = {"schema": probe.PREREG_SCHEMA,
                   "written_before_any_scored_run": True}
        if missing is not None:
            payload["written_before_any_scored_run_basis"] = missing
        repo, sha = _prereg_repo(tmp_path / ("none" if missing is None
                                            else "blank"), payload)
        monkeypatch.setattr(probe, "ROOT", repo)
        record, why = probe.read_preregistration(
            sha, Path("docs/evidence/7949-capstone/preregistration.json"))
        assert record is None, record
        assert fragment in why, why


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


# ── F3 (#9089 review): a MALFORMED pre-registration refuses, never crashes ──

def _prereg_repo(tmp_path: Path, payload: object) -> tuple[Path, str]:
    """A throwaway repo whose ONE commit carries ``payload`` as the prereg.

    The record's producer is another lane (#9075), so its SHAPE is untrusted
    input. A real commit is used (not a stubbed ``_git``) because the property
    under test is that G0 refuses a shape `git show` hands it verbatim.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, _parent, _child = _ancestry_pair(tmp_path)
    artifact = repo / "docs/evidence/7949-capstone/preregistration.json"
    artifact.write_text(json.dumps(payload))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "prereg"],
                   check=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True,
                         check=True).stdout.strip()
    return repo, sha


def test_a_prereg_record_that_is_not_an_object_is_refused_not_a_traceback(
        tmp_path, monkeypatch):
    """A root of ``[]`` used to raise ``AttributeError`` out of ``main()``.

    ⛔ The failure mode is not "an ugly traceback": the acceptance command is
    ``capstone_run.py … | tee docs/evidence/7949-capstone/capstone.log``, so a
    traceback (stderr only, exit 1) TRUNCATES the committed evidence file to an
    empty log and leaves no table saying why — the artifact the next lane reads
    is destroyed by the gate that exists to refuse rather than guess.

    Non-vacuity: the pre-fix reader calls ``record.get("schema")`` on a list, so
    this test raises instead of asserting (and ``out`` is never written).
    """
    repo, sha = _prereg_repo(tmp_path, [])
    monkeypatch.setattr(probe, "ROOT", repo)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "capstone.log"
    code = probe.main(["--prereg", sha, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert record["run"]["status"] == "REFUSED"
    assert "not an object" in record["run"]["reason"], record["run"]["reason"]
    # The refusal is a G0 refusal — the prereg was looked at and unusable.
    assert record["prereg"]["status"] == "ABSENT"
    # ⛔ V4 IS EXPECTED: the rows cite the artifact this run actually wrote (the
    # P1), which is a tmp file outside the repo. Everything ELSE must parse.
    assert {row["evidence"] for row in record["rows"]} == {str(out)}
    findings = cvc.check(record, repo=_REPO, require_green=False)
    assert not [item for item in findings.items if item[0] != "V4"], findings.render()
    assert "V9" in _codes(record, require_green=True)


def test_a_prereg_whose_corpus_block_is_not_an_object_is_refused(
        tmp_path, monkeypatch):
    """``corpus: "nope"`` made ``frozen.get(...)`` raise in G1, after G0 passed.

    Non-vacuity: the pre-fix reader returns the record, so ``main()`` reaches
    G1 and raises ``AttributeError`` on the string — no table, exit 1.
    """
    repo, sha = _prereg_repo(tmp_path, _frozen_prereg(corpus="nope"))
    monkeypatch.setattr(probe, "ROOT", repo)
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    out = tmp_path / "capstone.log"
    code = probe.main(["--prereg", sha, "--corpus", str(corpus),
                       "--out", str(out)])
    assert code == 2, code
    record = cvc.extract_block(out.read_text())
    assert "corpus as a str" in record["run"]["reason"], record["run"]["reason"]


def test_a_prereg_whose_corpus_items_is_not_a_count_is_refused(
        tmp_path, monkeypatch):
    """``items: "fifteen"`` (or a float, or a bool) is not a turn count.

    Non-vacuity: the pre-fix G1 does ``int(frozen["items"])`` inline, so a
    non-numeric truthy value raises ``ValueError`` out of ``main()`` — no
    table, exit 1, and ``tee`` truncates the shipped log.
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    sha_of = probe.corpus_sha256(corpus)
    for index, (bad, fragment) in enumerate((("fifteen", "not a turn count"),
                                            (True, "BOOLEAN"),
                                            (1.5, "fractional turn count"))):
        repo, sha = _prereg_repo(tmp_path / f"bad-{index}",
                                _frozen_prereg(
                                    corpus={"sha256": sha_of, "items": bad}))
        monkeypatch.setattr(probe, "ROOT", repo)
        out = tmp_path / f"capstone-{fragment.split()[0]}.log"
        code = probe.main(["--prereg", sha, "--corpus", str(corpus),
                           "--out", str(out)])
        assert code == 2, (bad, code)
        record = cvc.extract_block(out.read_text())
        assert fragment in record["run"]["reason"], (bad, record["run"]["reason"])


def test_a_prereg_that_freezes_a_numeric_string_count_still_measures(
        tmp_path, monkeypatch):
    """``items: "1"`` is a count a producer may legitimately emit — not refused.

    The guard must not become a NEW false refusal: a JSON producer that writes
    counts as strings is normalised to an int by the reader, and G1's own
    turn-count comparison then does the deciding (1 == 1 passes, 2 fails).
    """
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"role": "user", "content": "hello"}]))
    _read_prereg_ok(monkeypatch, _frozen_prereg(
        corpus={"sha256": probe.corpus_sha256(corpus), "items": "1"}))
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    out = tmp_path / "g2.log"
    code = probe.main(["--prereg", HEAD, "--corpus", str(corpus),
                       "--out", str(out)])
    # G1 passed (the count matched) and the run reached G2 — the embedded-store
    # refusal, NOT a "pre-registration is malformed" refusal.
    record = cvc.extract_block(out.read_text())
    assert code == 3, (code, record["run"]["reason"])
    assert record["run"]["gate_failed"] == 3
    assert record["prereg"]["status"] == "OK"


def test_a_row_that_blames_the_landed_pre_registration_says_it_landed():
    """#9075 closed COMPLETED (#9507) — a re-run's row must not print it bare.

    `#9075` is the ``blocked_by`` of every row on a PRE-G0 refusal, so a table
    re-generated after the landing would hand the next reader a closed issue as
    the work to do — the durable-misattribution rule ``LANDED_LANES`` already
    fixes for a criterion lane, applied to a GATE. The shipped log keeps its
    pre-landing stamp (and its bare `#9075`), which is why this is pinned on the
    ROWS the probe emits now, not on the artifact.

    Non-vacuity: pre-fix, the E5 note names `#9075` with no state at all.
    """
    assert probe.LANDED_GATES == {"#9075": "#9507"}, probe.LANDED_GATES
    for prereg_ok in (False, True):
        rows = {r["criterion"]: r for r in probe.refused_rows(
            "gate refused: x", prereg_ok=prereg_ok)}
        blamed = [r for r in rows.values() if r["blocked_by"] == "#9075"]
        assert blamed, (prereg_ok, rows)
        for row in blamed:
            assert "#9075 LANDED (#9507)" in row["note"], (prereg_ok, row["note"])


def test_the_probes_line_citations_resolve_too():
    """The README is pinned; the PROBE carried the same numbers with no pin at all.

    #9089 review P2: the probe docstring cites the same entry point and the same step call sites
    (nine cites over the eight steps, after the S2.3 pair was split — see below), and
    `test_the_readmes_line_citations_resolve_to_the_symbols_they_name` reads only
    `README.md` — so a merge-up that moves `sdk.py` re-stales the probe silently, which is exactly
    how the earlier rounds went stale. Same check, other file.

    #10214 review P2 (round 8): that same failure had ALREADY recurred one layer down. The eight
    BARE step cites (`` ``run_s1`` (S1, ``:8540``) ``) were never read by anything — the README's
    twin check pins only the README's copy — so a merge of `main` that added 88 lines to
    `extractor_v2.py` re-staled all eight by exactly +88 and every gate stayed green. The paragraph
    below the list even claimed they were "pinned only where the README's step table repeats the same
    numbers", which nothing compared. The README's own step-table check is the right shape, so this
    test now applies it here too: each `` ``symbol`` (LABEL, ``:NNNN``) `` in the header must name a
    line that CARRIES that symbol. The claim of coverage is now true rather than asserted.

    Non-vacuity: at the pre-fix head every one of the eight cited +88 lines is unrelated code
    (`:8507` is `extract_session_v2`'s docstring prose, `:8646` is a blank line, `:8845` a comment
    about `aboutObject`), so all eight assertions red; `sdk.py:11611` is a comment inside an
    unrelated M2 paragraph, so the call-site assertion reds as well.

    #10214 review (blocking): the cites were previously carried as a PAIR —
    `` ``kind_classifier.classify_items``/``_apply_classify_kinds`` (S2.3, ``:8700``) `` — and one
    line number cannot be right for two symbols on two different lines. This loop reads the LAST
    symbol of a pair, so it asserted `_apply_classify_kinds` against `classify_items`' line and the
    pin red-ed in CI while the README's one-symbol row (`:8701`) stayed right. The pair is now two
    cites on their own lines, so BOTH symbols are pinned, and the population is 9 (eight steps;
    S2.3 makes two calls). Non-vacuity of the fix: restoring the pair at `:8700` reds this loop on
    both counts — the population assert (8 vs 9) and `_apply_classify_kinds` against `:8700`.
    """
    probe = PROBE.read_text()
    sdk = (_REPO / "tortoise/sdk.py").read_text().splitlines()
    extractor = (_REPO / "tortoise/extractor_v2.py").read_text().splitlines()

    entry = re.search(r"``sdk\.py:(\d+)``, called at ``:(\d+)``", probe)
    assert entry, "the probe's sdk.py cite moved; re-derive it, do not drop the pin"
    def_line, call_line = (int(n) for n in entry.groups())
    assert sdk[def_line - 1].lstrip().startswith("def _extract_session_v2("), (
        f"probe cites the def at sdk.py:{def_line}, which is {sdk[def_line - 1].strip()!r}")
    assert "self._extract_session_v2(" in sdk[call_line - 1], (
        f"probe cites the call at sdk.py:{call_line}, which is {sdk[call_line - 1].strip()!r}")

    module_cite = re.search(r"``extractor_v2\.py:(\d+)``", probe)
    assert module_cite, "the probe's extractor_v2 entry cite is gone"
    module_line = int(module_cite.group(1))
    assert extractor[module_line - 1].lstrip().startswith("def extract_session_v2("), (
        f"probe cites the entry point at extractor_v2.py:{module_line}, which is "
        f"{extractor[module_line - 1].strip()!r}")

    # (b) The nine step call cites: `` ``symbol`` (LABEL, ``:NNNN``) `` into the same file, ONE
    # cite per symbol on the line that symbol is on. The
    # line WRAPS (`run_s4` and `_apply_classify_kinds` split their paren group), so the text is
    # whitespace-normalised before matching — the unit is the cite, not the physical line.
    flat = re.sub(r"\s+", " ", probe)
    # The entry-point cite is FILE-NAMED and asserted above; a bare step cite has no file name inside
    # the parenthesis, which is how the two shapes are told apart here.
    steps = [
        (symbol, int(lineno))
        for symbol, inner, lineno in re.findall(
            r"``([A-Za-z_][\w.]*)``\s*\(([^()]*?)``:(\d+)``\)", flat)
        if ".py:" not in inner
    ]
    assert len(steps) == 9, (
        f"the probe's nine step cites are gone (eight steps; S2.3 makes two calls): {steps}")
    for symbol, lineno in steps:
        text = extractor[lineno - 1]
        assert symbol.split(".")[-1] in text, (
            f"probe cites `{symbol}` at extractor_v2.py:{lineno}, which reads "
            f"{text.strip()!r} — the line moved and the cite did not")


# ── V7 across a multi-artifact table (the capstone's own rows) ────────────

def _e1e2_corpus_sha() -> str:
    """The digest ``e1-e2.log`` records for the corpus IT measured.

    Read from the artifact, never typed: the point of these tests is that a row's
    declared corpus is checked against the file, so a hard-coded digest here would
    be the same unverifiable claim one level up.
    """
    text = (_REPO / "docs/evidence/7949-capstone/e1-e2.log").read_text()
    match = re.search(r"corpus_sha256=([0-9a-f]{64})", text)
    assert match, "e1-e2.log no longer records its corpus digest"
    return match.group(1)


def _measured_row(**over) -> dict:
    """A NON-GREEN measured row citing a real artifact on ANOTHER corpus."""
    row = {
        "criterion": "E1", "verdict": "RED", "evidence_kind": "measurement",
        "evidence": "docs/evidence/7949-capstone/e1-e2.log",
        "sha": HEAD, "numerator": 2, "denominator": 15,
        "corpus_sha256": _e1e2_corpus_sha(),
        "sibling": None, "blocked_by": "#8503",
        "note": "measured on the #9076 arm run, whose corpus is not the frozen one",
    }
    row.update(over)
    return row


def _multi_artifact_record(row: dict | None = None) -> dict:
    rec = _record()
    rec["corpus"] = {"path": "tests/eval/write_path", "sha256": "f" * 64}
    rec["rows"] = [dict(r) for r in rec["rows"]]
    rec["rows"][0] = row or _measured_row()
    return rec


def test_a_non_green_row_on_another_corpus_must_declare_that_revision():
    """A row whose number comes from a DIFFERENT artifact must say so.

    The capstone's rows do not all come from one run: E1/E2 cite #9076's arm run
    and E4 an operator receipt, and neither measured the corpus the capstone run
    froze. Refusing those rows outright would hide the only numbers they have;
    accepting them on the row's word would let any red row name any corpus. So the
    row DECLARES the revision, and the declaration is checked against the artifact.
    """
    codes = _codes(_multi_artifact_record())
    assert "V7" in codes, codes


def test_a_declared_corpus_the_artifact_never_records_is_refused():
    """The declaration must be SUPPORTED by the cited file, not merely asserted.

    Without this arm a row could invent ``artifact_corpus_sha256`` and pass — the
    declaration would be one more unverifiable claim, which is the exact shape V4
    exists to refuse for the artifact itself.
    """
    row = _measured_row(artifact_corpus_sha256="a" * 64)
    codes = _codes(_multi_artifact_record(row))
    assert "V7" in codes, codes


def test_a_declared_corpus_that_disagrees_with_the_row_is_refused():
    """One number decides a row's corpus, and two copies that differ pick neither."""
    row = _measured_row(artifact_corpus_sha256="b" * 64)
    row["corpus_sha256"] = _e1e2_corpus_sha()
    codes = _codes(_multi_artifact_record(row))
    assert "V7" in codes, codes


def test_a_non_green_row_declaring_the_artifacts_own_corpus_is_clean():
    """The honest shape passes: the digest the row declares IS in the artifact."""
    record = _multi_artifact_record(_measured_row(
        artifact_corpus_sha256=_e1e2_corpus_sha()))
    codes = _codes(record)
    assert "V7" not in codes, codes


def test_the_declaration_arm_never_relaxes_GREEN():
    """A GREEN row on another corpus is refused WHATEVER it declares.

    This is the line the multi-artifact arm must not cross: a green row on a
    corpus the run did not freeze is the failure V7 exists for, and a declaration
    cannot buy it. (GREEN also needs 8 observed steps + a completed run, so this
    record reds on V5 as well — V7 is the code under test.)
    """
    row = _measured_row(
        verdict="GREEN", blocked_by=None,
        artifact_corpus_sha256=_e1e2_corpus_sha(),
        corpus_sha256=_e1e2_corpus_sha())
    record = _multi_artifact_record(row)
    record["run"] = {"status": "COMPLETED", "steps_observed": list(cvc.STEPS)}
    record["prereg"] = {"sha": HEAD, "status": "OK",
                        "artifact": "docs/evidence/7949-capstone/preregistration.json"}
    codes = _codes(record)
    assert "V7" in codes, codes


def test_a_refused_measurement_is_not_a_red_row():
    """E1's own shape: a proxy number that cannot be scored is BLOCKED, not RED.

    The e1-e2 artifact's 25 Objects are under the frozen floor of 30, so the
    record's own ``below_floor`` rule makes the measurement REFUSED — "neither a
    pass nor a fail". A table that wrote RED there would report a verdict the
    measurement is not entitled to; a table that wrote GREEN would be worse. The
    shard this pins is the vocabulary: ``REFUSED`` must never leave the probe as
    RED or GREEN.
    """
    measure = _load_measure_probe()
    result = measure.outcome(measured=True, below_floor=True, passed=False,
                             bar="after <= 0.5 x before", value="25 objects")
    assert measure.verdict_word(result) == "BLOCKED", result


def _load_measure_probe():
    """Load the MEASUREMENT probe by path (the evidence dir is hyphenated)."""
    path = _REPO / "docs/evidence/7949-capstone/probes/capstone_measure.py"
    spec = _ilu.spec_from_file_location("capstone_measure_7949", str(path))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_corpus_prefix_alone_is_not_a_different_corpus():
    """`sha256:<hex>` and `<hex>` are ONE revision, not two.

    The record, `e1-e2.log` and the operator receipt each spell the digest their
    own way. A raw string comparison reported such a row as "measured on another
    corpus" and demanded a declaration for a formatting difference — a false
    finding on a correct table, which is the direction this gate must never take.
    """
    digest = "d" * 64
    record = _record()
    record["corpus"] = {"path": "tests/eval/write_path", "sha256": f"sha256:{digest}"}
    record["rows"] = [dict(r) for r in record["rows"]]
    record["rows"][2] = _measured_row(criterion="E3", evidence="docs/evidence/7949-capstone/e1-e2.log",
                                     corpus_sha256=digest, numerator=1, denominator=2)
    codes = _codes(record)
    assert "V7" not in codes, codes
