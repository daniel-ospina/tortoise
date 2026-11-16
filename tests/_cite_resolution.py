"""ONE resolver for `file.py:LINE` citation pins — the drift is NAMED, not re-derived.

⛔ THE PROBLEM THIS EXISTS FOR. An absolute line number is **derived data stored as
a literal**, so every commit above it invalidates it:

    #10226 deleted 1,759 lines from tortoise/sdk.py  -> every cite below it drifted
    #12877 re-derived them by reading the file        -> green
    #12882/#12904/#12959/#12915 edited sdk.py         -> red again
    (#12915's 100-file change is the one that left `main` red at `e61694255a`)

Each repair was a lane that grepped `sdk.py`, matched a symbol to a line, and typed
the answer into a document and (often) into the pin beside it. The repair is not the
problem — the problem is that the pin reported **`sdk.py:19326 reads '# #4716 Part 1:
rewrite payload endpoint refs…'`**, which says what is WRONG but not what is RIGHT,
so a fixer had to re-derive by hand what the test already knew how to compute.

WHAT THIS DOES, AND WHAT IT DOES NOT. The pin already carries the ANCHOR — the code
fragment the cite claims is on that line. This resolves the anchor against the source
and puts the live line in the failure message:

    README.md:340 cites sdk.py:19326, and that line reads '# #4716 Part 1: …'.
    'the meta fold that calls the #9116 seam' is at sdk.py:19449 — re-point the cite.

So a drift stays RED (the doc's number is still checked, exactly as before: the
assertion is NOT weakened to "the file exists") and the repair becomes mechanical:
copy the line the message names. The next lane does not read `sdk.py` at all.

⚠️ THE LIMIT, stated so no reader over-reads this. A doc that stores a number can
still go stale; making it structurally impossible is the SYMBOL form, which this repo
already implements for a document that made the same journey —
`tests/test_extractor_v4_citation_rot.py`'s `_SYMBOL_CITATION`
(`module.py::symbol`) plus its AST `_symbol_span` resolver, adopted for
`EXTRACTOR-V4-ARCHITECTURE.md` §13/§13.8 and `STORAGE-ARCHITECTURE.md` §9.1 after
their line cites rotted twice. That is the end state for a document whose every cite
names a symbol; it is NOT a mechanical substitution here, because (a) ~100 of the
capstone bundle's cites name a LINE OF CODE rather than a binding (`"stats":
_capture_stats_with_step_witness(...)`, a `proj.link_source_to_event(` call, a range
across a dict literal) and have no symbol to name, and (b) several anchors resolve to
MORE THAN ONE line (`subject_binding_available,` is at `sdk.py:10353` and `:42329`),
so a symbol-only resolution would bind the wrong line silently — the exact failure
`#10214` fixed for the README's pair cites. This module is the half that can be built
without weakening any pin; the symbol form remains the target for the cites that can
carry it.
"""
from __future__ import annotations

#: How a line satisfies an anchor.
#:
#:   line   — the anchor appears IN the line (the commonest: a statement fragment)
#:   def    — the line, stripped, STARTS WITH the anchor (a `def`/`class` claim)
#:   exact  — the line, stripped, IS the anchor (a one-line block terminator)
ANCHOR_KINDS = ("line", "def", "exact")


def anchor_lines(source_lines, anchor: str, kind: str = "line") -> list[int]:
    """The 1-based lines of `source_lines` that satisfy `anchor` under `kind`."""
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"unknown anchor kind {kind!r}; one of {ANCHOR_KINDS}")
    if kind == "def":
        return [n for n, text in enumerate(source_lines, 1)
                if text.lstrip().startswith(anchor)]
    if kind == "exact":
        return [n for n, text in enumerate(source_lines, 1)
                if text.strip() == anchor]
    return [n for n, text in enumerate(source_lines, 1) if anchor in text]


def cite_holds(source_lines, number: int, anchors, kind: str = "line") -> bool:
    """Does line `number` satisfy EVERY anchor? (all-of: a pair claims both)

    ⛔ `kind` is VALIDATED here too, not only in `anchor_lines`. An unknown kind must
    never fall through to the `a in text` branch: that is the WEAKEST check, so a
    typo'd kind (`"exact "`, `"Span"`) would make the pass/fail decision silently
    laxer — a false PASS on the one comparison a pin exists for — while the failure
    message raised. One contract, both halves.
    """
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"unknown anchor kind {kind!r}; one of {ANCHOR_KINDS}")
    if not 1 <= number <= len(source_lines):
        return False
    text = source_lines[number - 1]
    return all(
        (text.lstrip().startswith(a) if kind == "def"
         else text.strip() == a if kind == "exact"
         else a in text)
        for a in anchors
    )


def locate_hint(source_lines, anchor: str, kind: str = "line",
                cited: int | None = None) -> str:
    """Where `anchor` actually lives, phrased to be pasted into a failure message.

    `cited` makes a multi-hit anchor actionable rather than merely truthful: an
    insertion ABOVE a cite moves it by a few lines, so the candidate NEAREST the
    cited number is the one the passage means, and it is named first. Every other
    candidate is still printed — an anchor that matches two lines is a weak anchor
    and the reader has to see that, not have it averaged away.
    """
    found = anchor_lines(source_lines, anchor, kind)
    if not found:
        return (f"appears NOWHERE in this file — the symbol was renamed, moved to "
                f"another module, or deleted, so re-verify the PASSAGE rather than "
                f"re-pointing the number")
    if len(found) == 1:
        return f"is at line {found[0]}"
    if cited is not None:
        ordered = sorted(found, key=lambda n: (abs(n - cited), n))
        rest = ", ".join(str(n) for n in ordered[1:])
        return (f"is at line {ordered[0]} (nearest of {len(found)} candidates for "
                f"this anchor; the others are {rest} — if the passage means one of "
                f"those, its anchor is not unique enough to pin it)")
    return f"is on {len(found)} lines ({', '.join(map(str, found))})"


def cite_failure(doc_label: str, source_label: str, number: int, source_lines,
                 anchors, *, kind: str = "line", what: str = "") -> str | None:
    """`None` when the cite holds; otherwise the message that names BOTH lines.

    `anchors` is a tuple: the cite is checked against ALL of them (a pair cite
    claims both symbols are on that line — the `all` rule `#10214` review fixed).
    `what` is the passage's own name for the thing cited (`the entry point`,
    `the references writer`), so the message reads as a sentence about the claim
    rather than about a substring.
    """
    if isinstance(anchors, str):
        anchors = (anchors,)
    if cite_holds(source_lines, number, anchors, kind):
        return None
    subject = f" ({what})" if what else ""
    if number < 1:
        return (f"{doc_label} cites {source_label}:{number}{subject}, which is not a "
                f"line of {source_label} at all (lines are 1..{len(source_lines)}) — "
                f"the number counts a different revision, or it names the wrong file")
    if number > len(source_lines):
        return (f"{doc_label} cites {source_label}:{number}{subject}, which is PAST "
                f"THE END of {source_label} ({len(source_lines)} lines) — the number "
                f"counts a different revision, or it names the wrong file")
    got = source_lines[number - 1].strip()
    hints = [f"`{a}` {locate_hint(source_lines, a, kind, number)}" for a in anchors]
    resolved = any(anchor_lines(source_lines, a, kind) for a in anchors)
    # ⛔ The re-point instruction is only TRUE when the anchor resolves SOMEWHERE.
    # Appending it to the `appears NOWHERE` case told a fixer to re-point a number at
    # a line that does not exist, immediately after telling them not to — the message
    # has to carry the next ACTION, and for a renamed symbol the action is to
    # re-verify the passage (a re-pointed number would bury a false claim).
    return (f"{doc_label} cites {source_label}:{number}{subject}, and that line reads "
            f"{got!r}. The thing it names " + "; ".join(hints)
            + (" — re-point the cite to that line (do not delete it)" if resolved else ""))
