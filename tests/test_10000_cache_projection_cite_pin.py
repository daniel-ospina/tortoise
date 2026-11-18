"""`06-cache-projection.md`'s `### The pin` list cites tests that must EXIST.

WHY THIS SURVIVES `#13439` WHEN THE PIN MODULE AROUND IT DID NOT. `#13439` retires
a doc↔code test that asserts a DOCUMENT matches the code as it is today
(`CONTRIBUTING.md` → "A test asserts behaviour; a document states the target").
This is not that class. The section states **what the pin is** — the legs that
make the doc's claim true or false — and it names each leg by TEST NAME. A name
that does not resolve is a prose reference that reads as proof and pins nothing
(`#7948`/`#10852`'s lesson: a hand-maintained prose reference rots and nothing
notices). The rule `#13439` lands keeps exactly this class: an
**invariant-protecting** arm — the document may state a target, but its pointer
to the proof must be real — is the one that STAYS, with this docstring saying why.

WHY ITS OWN FILE, REGISTERED IN `tier1`. The guarded artifact is under `docs/`, and
a docs-only diff selects NO surface and runs `tier1` only — so a pin placed inside
`tests/test_10000_source_prose.py` (`core`, graph-touching: one throwaway FalkorDB
per case) would SLEEP on exactly the diff that ADDS a cite. Same shape and the same
reason as `tests/test_10191_record_move_doc_pin.py`. Hermetic: file reads only — no
fixture, no DB, no network, no model.

WHAT IT DOES NOT DO. It does not assert the section's WORDING, its repair-route
list, or its citation style — those were `#10852`'s state-locking arms and are
retired with the module that held them (`#13554` carries the rest of that class).
It asserts one thing, in one direction: every `test_*` the section names is
defined. It also derives the universe of definitions from `tests/*.py` rather than
a hand-spelled module list, so a test that MOVES or is RENAMED cannot leave the
arm green about a name it no longer defines.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "epics" / "2026-10-10-7879-postgres-record"
       / "06-cache-projection.md")
PIN_HEADING = "### The pin"
TESTS = ROOT / "tests"

#: A bare `test_*` NAME. The lookbehind skips a path (`tests/this_file.py` — the
#: section cites module paths too) and the lookahead skips a FILENAME, so the arm
#: resolves the thing that must exist rather than the file it lives in. Measured:
#: matching the bare shape without those guards red the guard on its own path
#: instead of on a cited leg (the same arm the write-site guard carries in
#: `tests/test_10000_source_prose.py`).
_CITED = re.compile(r"(?<![/\w])(test_[a-z0-9_]+)(?!\.py)")
_DEFINED = re.compile(r"^def (test_[a-z0-9_]+)\(", re.MULTILINE)


def _pin_section() -> str:
    """The body of the ONE `### The pin` section, verbatim."""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.rstrip() == PIN_HEADING]
    assert len(starts) == 1, (
        f"`{DOC.name}` has {len(starts)} `{PIN_HEADING}` headings, expected "
        "exactly one — a reworded or duplicated heading leaves this arm "
        "inspecting the wrong prose while it reads as coverage")
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("### ")), len(lines))
    return "\n".join(lines[start:end])


def test_every_test_name_the_pin_section_cites_is_defined():
    """A cited leg that does not exist is a prose reference, not a pin."""
    pin = _pin_section()
    cited = sorted(set(_CITED.findall(pin)))

    # The CONTROL: a section reworded to cite nothing would pass the loop below
    # vacuously, and the whole point of the section is that it names its legs.
    assert cited, (
        f"`{DOC.name}`'s `{PIN_HEADING}` section cites no `test_*` name — the "
        "section is supposed to point at the legs that make its claim true or "
        "false. Re-derive what the pin is before trusting this arm")

    defined: set[str] = set()
    for path in sorted(TESTS.glob("*.py")):
        defined.update(_DEFINED.findall(
            path.read_text(encoding="utf-8", errors="replace")))

    missing = [name for name in cited if name not in defined]
    assert not missing, (
        f"`{DOC.name}`'s `{PIN_HEADING}` cites {missing}, which no "
        f"`tests/*.py` defines. A cited leg that does not exist pins nothing: "
        "re-point the cite at the test that now carries the claim (do not delete "
        "the citation, and do not rename the test back to satisfy the doc)")
