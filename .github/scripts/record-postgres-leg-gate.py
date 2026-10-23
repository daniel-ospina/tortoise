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

#: The two legs this job exists for, by name: a deselect is not a pass.
ARMED_LEG_PREFIX = "test_the_public_capture_path"
MIN_ARMED_LEGS = 2


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
    stats = {
        "tests": tests,
        "skipped": skipped,
        "xfailed": xfailed,
        "failed": failed,
        "armed": len(armed),
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
    if len(armed) < MIN_ARMED_LEGS:
        problems.append(f"only {len(armed)} of the {MIN_ARMED_LEGS} armed capture "
                        "legs ran — the Postgres proof was deselected (#9551)")
    if failed:
        problems.append(f"{failed} failing/erroring leg(s) — see the run step")
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
          f"(real_skips={stats['armed_real_skips']})")
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
