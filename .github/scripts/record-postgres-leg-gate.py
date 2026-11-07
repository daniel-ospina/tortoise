#!/usr/bin/env python3
"""Did the armed record leg (`test-record-postgres`) actually CERTIFY anything?

#9551's subject is "every Postgres leg SKIPPED, so the schema↔producer
disagreement the leg exists for had no CI failure path". A job is green when the
legs skip (pytest treats a skip as a pass), so the job must assert the legs RAN.
This is that assertion, read from the junit XML — an attribute is a result, a
count printed from a log summary is a rendering.

⛔ WHY THIS IS A FILE (#9772 review P2). It used to be an inline heredoc in
`python-ci.yml`, pinned only by two substring tests over the step's `run:`
(`"skipped" in runs and "exit 1" in runs`). **Those substrings survive the
deletion of the clause that actually fails the job**, so the pin could not
discriminate "asserts zero skips" from "prints a skip count": measured, with
`if skipped:` removed the step still matched both (`"skipped"` appears 8× in the
surviving text, `"exit 1"` in the missing-junit guard) and the job went GREEN on
a run in which neither Postgres leg executed. Behaviour that only exists inside
a YAML string cannot be tested; `decide()` can, against fixture XMLs
(`tests/test_9078_record_written_by_production_path.py` §8).

Exit codes: 0 = certified, 1 = a problem (each named on its own `::error::`
line), 2 = the XML is missing or unreadable (nothing to certify).
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: The legs this job exists for, by NAME prefix: a deselect is not a pass.
ARMED_LEG_PREFIX = "test_the_public_capture_path"

#: ⛔ WHICH FILES must each contribute at least one armed leg — a SET, not a
#: count. A flat `armed >= 2` was the original rule and it was satisfiable with a
#: whole FILE deselected the moment this job ran more than one file: measured on
#: PR #9862, `tests/test_9555_narrative_carrier.py` alone carries three prefixed
#: names, so `armed = 3 >= 2` certified a run in which NEITHER
#: `tests/test_9078_…` leg executed — the #9551 hole ("the leg that would catch
#: it never ran") reopened silently.
#: And a COUNT of distinct files is the same defect one level down (PR #9862
#: review round 4): two files that are not the two this job arms would satisfy
#: `len(armed_files) >= 2`. The requirement is therefore the set: every file named
#: here must have RUN one armed leg, and a file removed from the job's pytest
#: argument list is a gate failure rather than a smaller certificate.
ARMED_LEG_FILES = frozenset({
    "test_9078_record_written_by_production_path",
    "test_9555_narrative_carrier",
})

#: ⛔ THE CASES WITH NO `ARMED_LEG_PREFIX` NAME, required BY NAME (#11429 review
#: cycle 2). The capstone's legs are named per DoD item (`test_D8_…`), so neither
#: rule above can see them: a whole-file deselect is caught by
#: `ARMED_LEG_FILES` only for the two files named there, and for the capstone
#: nothing catches it at all — the workflow still LISTS the file, so a
#: `-k "not D8"` / `--deselect …::test_D8_…` (the ordinary way a flaky leg is
#: quarantined) leaves `tests/test_9567_record_capstone.py` in the pytest argument
#: list, records NO skip, and the required job goes green with D8 — the only
#: restore drill on any CI leg — never executed. Measured before this rule: a
#: junit from the job's own armed legs plus the capstone run `-k "not D8"`
#: (tests=133, skipped=0, no D8 case) was CERTIFIED, exit 0.
#:
#: ⛔ AND THE RULE MUST MATCH THE CATEGORY IT STATES (#11429 review cycle 4).
#: Enumerating ONE entry fixed D8 and left the other eleven DoD legs — `test_D1_…`
#: .. `test_D12_…` — visible to the same `-k`: measured `-k "not D10"` (the drill
#: that `DROP POLICY`s on the live record, i.e. the leg most likely to be
#: quarantined) still CERTIFIED at exit 0. An enumeration is the defect whether it
#: holds one name or twelve, so the required set is DERIVED from the capstone's
#: own source (`test_D*`) and a new DoD leg is protected the day it is named.
#: Fails CLOSED: if the file cannot be read, nothing is certified.
#:
#: Each entry here is `case name` -> the wrong outcome its absence produces; it
#: carries the rationale for legs whose absence is a specific, named loss and is
#: UNIONED with the derived set (it is a subset today).
REQUIRED_CASES = {
    "test_D8_the_artifact_restores_into_a_scratch_record_and_the_falsifier_fires":
        "the record's restore drill (#9567 D8) never ran — the durable artifact "
        "is not proven restorable on this leg, and a `-k` filter hides it",
}

#: The capstone file whose `test_D*` legs are required by construction.
CAPSTONE_TEST_FILE = (Path(__file__).resolve().parents[2]
                      / "tests" / "test_9567_record_capstone.py")

#: ⛔ A REQUIRED LEG MUST COME FROM THE CAPSTONE'S OWN MODULE (#11429 cycle 6).
#: Matching on the case NAME alone let a same-named stub in ANY module the job
#: runs satisfy the requirement: measured, moving the single `test_D8_…` case to
#: `classname="tests.test_other"` and deselecting the capstone CERTIFIED (exit 0).
#: pytest allows the same function name in several modules, so nothing else
#: objects — the gate has to say where the leg must live. `_leg_module` is the
#: same reduction the armed-leg rule uses, so a `file=`/`classname=` shape resolves
#: identically here.
CAPSTONE_LEG_MODULE = CAPSTONE_TEST_FILE.stem

#: ⛔ THE STATIC HALF OF THE REQUIRED SET (#11429 review cycle 5). Deriving the
#: required legs ONLY from the capstone's source is circular: the rule exists to
#: stop a DoD leg vanishing, and deleting the `def test_D10_…` line deletes the
#: requirement with it — measured, a junit with every remaining leg, both armed
#: files, 0 skips and 0 failures CERTIFIED (exit 0) once D10 was deleted, and
#: again once it was merely renamed. So the requirement is `STATIC ∪ DERIVED`:
#: the tuple below catches a deletion or a rename (as `required leg … did not
#: run`), and the derivation keeps a NEW `test_D*` protected the day it is
#: written without a second edit here. Renaming a leg on purpose therefore reds
#: this gate — update this tuple in the same commit, which is the point.
EXPECTED_CAPSTONE_LEGS = (
    "test_D1_every_record_table_exists_on_the_armed_record",
    "test_D1_an_unset_dsn_reports_NOT_ARMED_and_writes_nothing",
    "test_D2_one_capture_writes_the_record_with_zero_write_failures",
    "test_D3_no_write_path_bypasses_the_record",
    "test_D4_the_restore_runs_with_the_journal_gone",
    "test_D5_D6_the_restore_reproduces_the_record_per_label_and_per_predicate",
    "test_D7_the_raw_turn_text_reads_back_from_the_record_only",
    "test_D8_the_artifact_restores_into_a_scratch_record_and_the_falsifier_fires",
    "test_D9_a_cross_tenant_read_is_empty_and_a_cross_tenant_write_is_refused",
    "test_D10_a_record_table_without_its_policy_fails_closed",
    "test_D11_the_gate_refuses_at_the_org_limit_and_the_refusal_is_visible",
    "test_D12_a_gate_with_no_usage_reading_fails_closed",
)


def _capstone_leg_names() -> list[str]:
    """Every DoD leg the capstone FILE defines, read from its source with `ast`.

    Source, not a list: a hand-maintained enumeration is how eleven of the twelve
    legs stayed invisible (#11429 review cycle 4), and a leg added later must not
    need a second edit here to be protected. `ast` rather than a regex so a name
    mentioned in a docstring or a comment is not mistaken for a definition.

    ⛔ Raises rather than returning `[]`: an unreadable or unparseable capstone is
    NOT "no required legs", it is "this gate cannot answer" — the same fail-closed
    rule as a missing junit XML (`main`).
    """
    import ast

    tree = ast.parse(CAPSTONE_TEST_FILE.read_text(encoding="utf-8"))
    return [node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_D")]


def _leg_module(case) -> str:
    """The MODULE an armed leg belongs to — bare name, no path, no package.

    `file` (`tests/test_x.py`) and `classname` (`tests.test_x`, or
    `tests.test_x.Class`) both reduce to `test_x`, which is what
    :data:`ARMED_LEG_FILES` names. The classname fallback drops trailing CLASS
    components only (a class is capitalised) — ⛔ it must NOT drop the last
    component unconditionally, which strips the MODULE name and collapses every
    file into `tests` (measured on this change's own fixtures).
    """
    explicit = case.get("file")
    if explicit:
        return Path(explicit).stem
    parts = (case.get("classname") or "").split(".")
    while len(parts) > 1 and parts[-1][:1].isupper():
        parts.pop()
    return parts[-1] if parts else ""


def decide(xml_path: Path) -> tuple[dict[str, int], list[str]]:
    """(stats, problems) for one junit XML. `problems` empty ⇒ certified.

    The root is `<testsuites>` on this pytest and `<testsuite>` on the xunit1
    shape the job asks for, so count over EVERY suite rather than reading the
    root's attributes: a first-match read of the root returned `None` here and
    reported "0 tests collected" on a green run (`int(None or 0)`), i.e. a
    rendering artifact dressed as a result.

    Skips are counted per TESTCASE, and only the ones that skipped for real. A
    strict xfail is recorded as `<skipped type="pytest.xfail">` and IS included
    in the suite's `skipped` attribute — and one of the two armed legs carries an
    xfail pinned to a MEASURED engine defect (`_fold_refuses_captured_points`),
    so counting the attribute would red this required job on the day that defect
    returns, blaming a database that armed correctly.
    """
    root = ET.parse(str(xml_path)).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(s.get("tests") or 0) for s in suites)
    failed = sum(int(s.get("failures") or 0) + int(s.get("errors") or 0)
                 for s in suites)
    skipped = xfailed = 0
    for case in root.iter("testcase"):
        node = case.find("skipped")
        if node is None:
            continue
        if node.get("type") == "pytest.xfail":
            xfailed += 1
        else:
            skipped += 1
    armed = [c for c in root.iter("testcase")
             if c.get("name", "").startswith(ARMED_LEG_PREFIX)]
    armed_files = sorted({_leg_module(c) for c in armed if _leg_module(c)})
    stats = {
        "tests": tests,
        "skipped": skipped,
        "xfailed": xfailed,
        "failed": failed,
        "armed": len(armed),
        "armed_files": len(armed_files),
        "armed_file_names": armed_files,
        # A leg that XFAILED did not skip for lack of a database, so the
        # report must not call it a skip: on a red, the one line an
        # operator reads is this one (#9772 review P2).
        "armed_real_skips": sum(
            1 for c in armed
            if (n := c.find("skipped")) is not None
            and n.get("type") != "pytest.xfail"),
    }
    problems: list[str] = []
    if tests == 0:
        problems.append("0 tests collected — nothing was certified")
    if skipped:
        problems.append(f"{skipped} leg(s) SKIPPED — the record database "
                        "did not arm, or the driver is missing (#9551)")
    missing_files = sorted(ARMED_LEG_FILES - set(armed_files))
    if missing_files:
        problems.append(
            f"no armed capture leg ran from {', '.join(missing_files)} — a whole "
            f"file's legs were deselected (ran: {', '.join(armed_files) or 'none'}), "
            "which neither a flat leg count nor a count of distinct files can see "
            "(#9551)")
    if failed:
        problems.append(f"{failed} failing/erroring leg(s) — see the run step")
    # ⛔ "PRESENT" MUST MEAN "PRODUCED A VERDICT", not "a testcase with this
    # name exists" (#11429 review cycle 3). A case that did not run carries a
    # `<skipped>` child — and `type="pytest.xfail"` is deliberately EXCLUDED from
    # the `skipped` counter above, because one ARMED leg carries a measured xfail.
    # Composed, a `@pytest.mark.xfail` on the D8 case — the ordinary quarantine
    # idiom, and the most likely thing anyone would quarantine here since the leg
    # runs a second container over the network — satisfied name-presence, recorded
    # no real skip, and CERTIFIED (exit 0) a run in which the record's only restore
    # drill never produced a verdict: the exact fail-open this rule exists to close,
    # one line away. No `REQUIRED_CASES` entry has a legitimate xfail.
    # ⛔ (NAME, MODULE), not name alone — see `CAPSTONE_LEG_MODULE`. A case that did
    # not run carries a `<skipped>` child, so it is not present (a skip is not a
    # verdict, and an `xfail` is a `<skipped type="pytest.xfail">`).
    present = {(case.get("name", ""), _leg_module(case))
               for case in root.iter("testcase")
               if case.find("skipped") is None}
    # UNIONED: the explicit rationales plus every DoD leg the capstone DEFINES.
    # A `-k "not D<k>"` deselects exactly one name and records no skip, so only a
    # per-name requirement can see it — and it has to be the whole set, not one
    # entry (#11429 review cycles 3 and 4).
    try:
        required = dict(REQUIRED_CASES)
        for leg in EXPECTED_CAPSTONE_LEGS:
            required.setdefault(
                leg,
                "a capstone DoD leg this gate names statically did not run — it "
                "was deleted or renamed, or a `-k`/`--deselect` filter hid it")
        for leg in _capstone_leg_names():
            required.setdefault(
                leg,
                f"the capstone's DoD leg {leg} never ran — a `-k`/`--deselect` "
                "filter hid it, and no other CI leg proves this item")
    except (OSError, SyntaxError, ValueError) as exc:
        problems.append(
            f"cannot read the capstone's DoD legs from {CAPSTONE_TEST_FILE} "
            f"({exc}) — nothing was certified (#11429)")
        required = dict(REQUIRED_CASES)
    for required_leg, why in sorted(required.items()):
        if (required_leg, CAPSTONE_LEG_MODULE) not in present:
            elsewhere = any(
                name == required_leg for name, _module in present)
            problems.append(
                f"required leg {required_leg} did not run in {CAPSTONE_LEG_MODULE}"
                + (" (a case of that NAME ran in another module — a required leg "
                   "must be the capstone's own)" if elsewhere else "")
                + f" — {why} (#11429)")
    return stats, problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <junit.xml>", file=sys.stderr)
        return 2
    xml_path = Path(argv[1])
    if not xml_path.is_file():
        print(f"::error::no junit XML at {xml_path} — the armed leg produced no "
              "result to certify (#9551)")
        return 2
    try:
        stats, problems = decide(xml_path)
    except (ET.ParseError, OSError, ValueError) as exc:
        # An XML that cannot be read certifies nothing. Never let a parse
        # failure look like "no problems found".
        print(f"::error::unreadable junit XML at {xml_path} ({exc}) — nothing "
              "was certified (#9551)")
        return 2
    print(f"tests={stats['tests']} skipped={stats['skipped']} "
          f"xfailed={stats['xfailed']} failed={stats['failed']} "
          f"armed_capture_legs={stats['armed']} "
          f"from_files={stats['armed_files']} "
          f"(real_skips={stats['armed_real_skips']})")
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
