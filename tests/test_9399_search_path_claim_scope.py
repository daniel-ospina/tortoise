"""#9399 — the "primary search path" claim must name the scope it was MEASURED over.

THE DEFECT THIS PINS
--------------------
Two design documents carry the sentence that the `aboutObject` ruling rests on,
and BOTH stated it unscoped:

`docs/architecture/STORAGE-ARCHITECTURE.md` §13:

    **The primary search path does NOT read it.** `rg -n 'aboutObject'
    tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py`
    -> exit 1, zero matches. Hindsight's premise holds for this path.

`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §13.8 question 2 (and its
§13.6 echo):

    The primary search path does **not** read it (`grep -in aboutObject
    tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py`
    -> exit 1, case-insensitive) — so Hindsight's premise holds *there*.

The measurement is TRUE **and the sentence is unscoped**. The command covers
exactly three modules, and `tortoise/ranking.py` — which holds both product
ranking read paths, and therefore the reads behind the default recall DOOR — is
not one of them. So the sentence is true of the default FTS/vector/RRF retrieval
*lane* and false of `tortoise_recall` / `recall_state`, which is the door a reader
would take "the primary search path" to mean (and the door whose reads made the
count decisive in the first place, #9399).

That is not a pedantic distinction: **the count is the whole argument** (the
storage document says so itself) and the ruling is KEEP the stored edges. A
reader who checks only the quoted command concludes the primary path never
touches the edge; a reader who stops there undercounts the blast radius of the
very change the sections rule on.

WHY BOTH DOCUMENTS, NOT ONE
---------------------------
`STORAGE-ARCHITECTURE.md` §13 and `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 are the
two places the issue names, and the extractor document is the one an extractor
implementer actually reads. Scoping one and leaving the other leaves the set
asserting OPPOSITE things about the same measurement — and a single-document
guard cannot see the copy it does not read, so the qualifier can disappear from
the un-guarded document exactly the way it was absent from §13. Hence `CLAIMS`
below: one entry per document, every arm runs over all of them.

WHAT THIS PINS
--------------
1. `test_the_quoted_command_is_true_over_the_files_it_names` — the claim is not
   merely asserted: the file list is parsed OUT of each document's sentence, it
   must still contain the three modules the prose calls "the whole of what was
   measured" (`EXPECTED_FILES`), each file must exist, and the scan is re-run over
   exactly those files (zero matches, case-insensitive). A widened list reds here
   (a module that does read the edge) and a NARROWED one reds too (the sentence
   would then claim the lane over a smaller measurement than the one it states).
2. `test_the_claim_names_the_module_it_excludes` — each sentence must state the
   scope it was measured over: `ranking.py` named as OUTSIDE the file list, the
   recall door named as the reading path, and the LANE named. This is the arm
   that is RED before the fix and GREEN after it — a test that passes without the
   fix proves nothing.
3. `test_no_document_asserts_the_unscoped_claim` — the presence arms above cannot
   catch a SECOND copy of the sentence elsewhere in the same document (that is
   how the extractor document carried it twice: a §13.6 verdict line and a §13.8
   answer). So the unscoped assertion is pinned as an ABSENCE, over every line of
   every document in `CLAIMS`. RED at the pre-fix head on both extractor lines.
4. `test_the_excluded_module_really_reads_the_edge` — non-vacuity for arms 2 and
   3: the named exclusions must actually read the edge, at HEAD, and must do it
   the WAY the sentences say (`ABOUT_EDGE_TYPES` joined into the query;
   `_ABOUT_TYPES` used inside each `recall_state` reader). Names alone would let a
   dead constant stand behind a citation.

NOT promised:
- No count is asserted here. The #7948 census table is #7948's to pin
  (`tests/test_7948_about_object_read_sites.py`); this file pins the SCOPE of the
  headline sentences only, so it is independent of how many rows the table ends
  up carrying and does not have to be re-cut when that number moves. That is why
  arm 3 pins the CLAIM SHAPE (`the primary search path does not read`) and not
  the surrounding numerals.
- The presence arms assert PRESENCE only, so an edit that says more than the
  minimum (a fuller citation, a re-derived count) stays green. The absence arm is
  deliberately narrow: it fires on the unscoped assertion ABOUT THIS EDGE (the
  read object is pinned to `it` / `the edge`), not on the phrase "primary search
  path" in general, and not on a sentence about a different object — that remains
  legitimate design vocabulary.
- The count of customer-callable reads is not asserted, so the sibling lanes that
  re-cut it do not have to re-cut this file.
- No absence assertion on other documents: the census table's siblings may
  legitimately restate the premise while their rows are being re-derived, and
  this file must not red on a PR whose subject is elsewhere. The documents it
  guards are the two the issue names.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs" / "architecture"

#: The two documents issue #9399 names. Both carry the claim; both are guarded.
STORAGE_DOC = DOCS_DIR / "STORAGE-ARCHITECTURE.md"
EXTRACTOR_DOC = DOCS_DIR / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The module the three-file measurement cannot see, and the door that reads the
#: edge through it. Both are asserted by name in each claim (arm 2).
EXCLUDED_MODULE = "ranking.py"
EXCLUDED_MODULE_PATH = ROOT / "tortoise" / "ranking.py"
SDK_PATH = ROOT / "tortoise" / "sdk.py"


@dataclass(frozen=True)
class Claim:
    """One document's copy of the claim, plus how to find and parse it.

    `anchor` locates the single line carrying the quoted measurement in THAT
    document. The quoted command is the anchor rather than the pinned wording:
    it appears in the claim BEFORE and AFTER the fix, so locating the claim does
    not depend on the words this file asserts.
    """

    label: str
    doc: Path
    anchor: str
    files_re: Pattern[str]


CLAIMS: tuple[Claim, ...] = (
    Claim(
        label="STORAGE-ARCHITECTURE.md §13",
        doc=STORAGE_DOC,
        anchor="rg -n 'aboutObject'",
        files_re=re.compile(r"rg -n 'aboutObject'\s+([^`]+)`"),
    ),
    Claim(
        label="EXTRACTOR-V4-ARCHITECTURE.md §13.8 question 2",
        doc=EXTRACTOR_DOC,
        anchor="grep -in aboutObject",
        files_re=re.compile(r"grep -in aboutObject\s+([^`]+)`"),
    ),
)

#: The assertion this issue is about: "the primary search path" — the whole
#: product — does not read the edge. `does`/`not`/`read` tolerate the markdown
#: emphasis the documents use (`does **not** read`), and every separator is `\s*`
#: / `\s+`, so the pattern spans a LINE WRAP: the documents are reflowed prose, so
#: a hard-wrapped copy of the sentence is the ORDINARY rendering, not a contrived
#: evasion — and it is matched against the whole document text for that reason.
#:
#: The read OBJECT is pinned (`it`, `the edge`, `the aboutObject edge`) so the arm
#: does not fire on ordinary design prose about a different object — *"the primary
#: search path does not read their mapping table"* is a legitimate sentence about
#: the comparable's mechanism, and a guard that reds on design prose is a guard
#: that gets deleted (the standard `tests/test_extractor_v4_citation_rot.py`
#: states for its own arms).
UNSCOPED_CLAIM_RE = re.compile(
    r"the\s+primary\s+search\s+path\s+does\s*\*{0,2}\s*not\s*\*{0,2}\s*read\s+"
    r"(?:it\b|the\s+(?:`?aboutObject`?\s+)?edge\b)",
    re.IGNORECASE,
)

#: The three modules every copy of the claim quotes as the WHOLE of what was
#: measured (the prose says so: *"the three files quoted below are the whole of
#: what was measured"*). Parsing the list out of the sentence (arm 1) is what
#: keeps the claim re-runnable; this constant is what keeps it the SAME
#: measurement — a list narrowed to one module would still re-run cleanly and
#: still assert the lane, so nothing else here would notice that the sentence had
#: stopped being true of the product.
EXPECTED_FILES: tuple[str, ...] = (
    "tortoise/retrieval.py",
    "tortoise/search_engine.py",
    "tortoise/session_reinjection.py",
)


def _claim_line(claim: Claim) -> str:
    """The single markdown line carrying this document's quoted measurement."""
    matches = [
        line
        for line in claim.doc.read_text(encoding="utf-8").splitlines()
        if claim.anchor in line
    ]
    assert matches, (
        f"{claim.label}: the quoted measurement is gone from {claim.doc.name} — no "
        f"line contains {claim.anchor!r}. The evidence for the KEEP ruling must "
        "stay re-runnable."
    )
    assert len(matches) == 1, (
        f"{claim.label}: expected exactly one line to carry the quoted "
        f"measurement, found {len(matches)} — an ambiguous anchor makes every arm "
        "below guess which claim it is checking"
    )
    return matches[0]


def _quoted_files(claim: Claim, line: str) -> list[str]:
    match = claim.files_re.search(line)
    assert match, (
        f"{claim.label}: the measured file list is not parseable out of the claim "
        "line; the sentence must keep the re-runnable command shape "
        f"`{claim.anchor} <files>`"
    )
    return match.group(1).split()


def test_the_quoted_command_is_true_over_the_files_it_names() -> None:
    """Re-run each document's quoted measurement, over exactly the files it names."""
    for claim in CLAIMS:
        files = _quoted_files(claim, _claim_line(claim))
        assert files, f"{claim.label}: the claim quotes a command with no file list"

        missing = [rel for rel in EXPECTED_FILES if rel not in files]
        assert not missing, (
            f"{claim.label}: the quoted file list no longer contains {missing} — "
            "that list is the scope the sentence claims to state ('the three "
            "files ... are the whole of what was measured'), so NARROWING it "
            "while the sentence still names the lane makes the claim wider than "
            "its own measurement (#9399)"
        )

        hits: list[str] = []
        for rel in files:
            path = ROOT / rel
            assert path.is_file(), (
                f"{claim.label} greps {rel!r}, which does not exist — the "
                "measurement can no longer be re-run by a reader"
            )
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "aboutobject" in line.lower():
                    hits.append(f"{rel}:{lineno}: {line.strip()}")

        assert not hits, (
            f"{claim.label} claims the quoted grep returns zero matches, but it "
            "now matches:\n  " + "\n  ".join(hits) + "\nEither the claim's file "
            "list is wrong or the lane does read the edge — both change the "
            "ruling's evidence and neither may land silently (#9399)."
        )


def test_the_claim_names_the_module_it_excludes() -> None:
    """Each sentence must scope itself, not read as a whole-product claim.

    RED before the fix: the pre-#9399 sentences name neither the module outside
    the measurement nor the door that reads through it, so a reader takes "the
    primary search path" to cover `recall` — which is false.
    """
    for claim in CLAIMS:
        claim_text = _claim_line(claim)
        files = _quoted_files(claim, claim_text)

        assert EXCLUDED_MODULE not in files, (
            f"{claim.label}: the claim's measured file list now contains "
            f"{EXCLUDED_MODULE} — if the measurement was widened, the sentence's "
            "own scoping must be re-derived rather than left describing the old "
            "three-file scope"
        )

        assert EXCLUDED_MODULE in claim_text, (
            f"{claim.label}: the claim does not name {EXCLUDED_MODULE}, the "
            "product ranking module that sits OUTSIDE its three-file measurement "
            "while reading the edge — so the sentence is unscoped and reads as a "
            "claim about the whole search path (#9399)"
        )

        assert re.search(r"recall\s+door", claim_text, re.IGNORECASE), (
            f"{claim.label}: the claim must name the recall DOOR as the path that "
            "does read the edge through the excluded module — otherwise a reader "
            "checking only the quoted grep concludes the primary recall path "
            "never touches it (#9399)"
        )

        assert re.search(r"\blane\b", claim_text, re.IGNORECASE), (
            f"{claim.label}: the claim must say which LANE the zero-match "
            "measurement bounds (the default FTS/vector/RRF retrieval lane) — the "
            "scope is the claim (#9399)"
        )


def test_no_document_asserts_the_unscoped_claim() -> None:
    """The presence arms cannot see a SECOND copy of the sentence in the same doc.

    That is exactly how this survived: the extractor document stated it twice, in
    a §13.6 verdict line and again in the §13.8 answer, so scoping the parsed
    claim line alone would leave the other asserting the removed premise. Both
    pre-fix lines contain "The primary search path does(...** not **)read", so
    this arm is RED at the pre-fix head (2 offenders) and GREEN after.

    Scanned over the WHOLE DOCUMENT TEXT rather than line by line: the documents
    are reflowed prose, so a later copy of the sentence can be wrapped mid-phrase
    (`the primary search path\ndoes not read the edge`), and a per-line scan —
    which is how this arm first shipped — reports that wrapped copy as clean
    while the sentence is right there on screen (review round 5, P2).
    """
    offenders: list[str] = []
    for claim in CLAIMS:
        text = claim.doc.read_text(encoding="utf-8")
        for match in UNSCOPED_CLAIM_RE.finditer(text):
            lineno = text.count("\n", 0, match.start()) + 1
            line = text.splitlines()[lineno - 1].strip()
            offenders.append(f"{claim.doc.name}:{lineno}: {line[:200]}")

    assert not offenders, (
        "a design document asserts that THE PRIMARY SEARCH PATH does not read the "
        "`aboutObject` edge, which is false of the default recall door "
        "(`tortoise_recall` -> `recall_state`, ranking through `tortoise/ranking.py`, "
        "which is outside the three files any such measurement covers). State the "
        "lane the measurement bounds and name the door outside it (#9399):\n  - "
        + "\n  - ".join(offenders)
    )


def _method_body(text: str, symbol: str) -> str:
    """One method's source text, from its `def` to the next method at that depth."""
    start = text.index(f"def {symbol}(")
    end = text.find("\n    def ", start)
    return text[start:] if end == -1 else text[start:end]


def test_the_excluded_module_really_reads_the_edge() -> None:
    """Non-vacuity: the exclusions arms 2 and 3 name must read the edge at HEAD."""
    ranking = EXCLUDED_MODULE_PATH.read_text(encoding="utf-8")
    assert "aboutObject" in ranking, (
        f"{EXCLUDED_MODULE_PATH.name} no longer mentions `aboutObject`; the §13 "
        "scoping claim (and the rows that cite this module) describe a read that "
        "does not exist — re-derive the claim instead of keeping a stale one"
    )
    assert re.search(r'ABOUT_EDGE_TYPES\s*=\s*\((?:[^)]*)"aboutObject"', ranking), (
        "`ABOUT_EDGE_TYPES` no longer carries `aboutObject`; the alternation the "
        "claim cites as the reason a literal grep misses the ranking reads is gone"
    )
    # …and it must be USED, not merely defined: the claim is that ranking reads
    # the edge *through* the alternation, so a dead constant would leave the
    # document's justification false while the assertion above stayed green.
    assert '"|".join(ABOUT_EDGE_TYPES)' in ranking, (
        "`ABOUT_EDGE_TYPES` is no longer joined into a Cypher alternation, so "
        "`ranking.py` no longer reads the edge THROUGH it — the reason the claim "
        "gives for a literal grep missing these reads no longer holds"
    )

    sdk = SDK_PATH.read_text(encoding="utf-8")
    assert re.search(r'_ABOUT_TYPES\s*=\s*"[^"]*aboutObject', sdk), (
        "`TortoiseSDK._ABOUT_TYPES` no longer carries `aboutObject`, so the "
        "`recall_state` enrichment readers the claim cites no longer read the edge"
    )
    for symbol in ("_state_related_objects", "_state_related_points"):
        assert f"def {symbol}(" in sdk, (
            f"`{symbol}` — the `recall_state` enrichment reader the claim names as "
            "reaching the edge through `_ABOUT_TYPES` — no longer exists"
        )
        # The LINKAGE, not just the two names: the claim is that `_ABOUT_TYPES`
        # *carries the type into* these readers. If one of them built its own
        # predicate (or delegated), the document would assert a connection that
        # no longer exists while every assertion above stayed green.
        assert "self._ABOUT_TYPES" in _method_body(sdk, symbol), (
            f"`{symbol}` no longer builds its predicate from `self._ABOUT_TYPES`, "
            "so the claim that the recall door reads the edge through that "
            "constant is false — re-derive the claim, do not keep the citation"
        )
