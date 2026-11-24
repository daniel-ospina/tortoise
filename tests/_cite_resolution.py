"""ONE resolver for `file.py:LINE` citation pins — the drift is NAMED, not re-derived.

⛔ THE PROBLEM THIS EXISTS FOR. An absolute line number is **derived data stored as
a literal**, so every commit above it invalidates it:

    #10226 deleted 1,759 lines from tortoise/sdk.py  -> every cite below it drifted
    #12877 re-derived them by reading the file        -> green
    #12882/#12904/#12959/#12915 edited sdk.py         -> red again
    (#12915's 100-file change is the one that left `main` red at `e61694255a`)

Each repair was a lane that grepped `sdk.py`, matched a symbol to a line, and typed
the answer into a document and (often) into the pin beside it. The repair is not the
problem — the problem is that the pin reported what the cited line READS and nothing
else — `sdk.py:19326 reads 'str(ev.get("eventKind", "core:occurrence"))…'` — which
says what is WRONG but not what is RIGHT, so a fixer had to re-derive by hand what
the test already knew how to compute.

WHAT THIS DOES, AND WHAT IT DOES NOT. The pin already carries the ANCHOR — the code
fragment the cite claims is on that line. This resolves the anchor against the source
and puts the live line in the failure message. The shape below is for a STALE cite
(this one is not: the README's own `sdk.py` cite is `19485` at this head, the meta
fold, which is why it is green):

    README.md:340 cites sdk.py:19326, and that line reads 'str(ev.get("eventKind",
    "core:occurrence"))…'. The thing it names '"stats": _capture_stats_with_step_witness'
    is at sdk.py:19485 — re-point the cite to that line (do not delete it).

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
MORE THAN ONE line (`subject_binding_available,` is at `sdk.py:10353` and `:41859`),
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

#: How many non-nearest candidates `locate_hint` enumerates. A count is always stated;
#: only the list is bounded, so a generic structural anchor (`exact '}'` matches 102
#: lines of `sdk.py`, `exact ')'` 465) cannot turn the actionable message into a wall.
_MAX_OTHER_CANDIDATES = 8


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


def locate_hint(source_lines, anchor: str, kind: str = "line",
                cited: int | None = None) -> str:
    """Where `anchor` actually lives, phrased to be pasted into a failure message.

    `cited` makes a multi-hit anchor actionable rather than merely truthful: an
    insertion ABOVE a cite moves it by a few lines, so the candidate NEAREST the
    cited number is the one the passage means, and it is named first. The other
    candidates are printed too — an anchor that matches two lines is a weak anchor
    and the reader has to see that, not have it averaged away — but the list is CAPPED
    (#13033 review P3): `rows` uses `("exact", ")")` and `_BUNDLE_RULES` uses
    `("exact", "}")` as an end spec, and those match 465 and 102 lines in `sdk.py`,
    so an uncapped list produced a 1 KB "nearest of 465 candidates" wall on exactly the
    structural closers where the advice is vacuous. The count is always stated; only the
    enumeration is bounded, and the truncation says so.
    """
    found = anchor_lines(source_lines, anchor, kind)
    if not found:
        return ("appears NOWHERE in this file — the symbol was renamed, moved to "
                "another module, or deleted, so re-verify the PASSAGE rather than "
                "re-pointing the number")
    if len(found) == 1:
        return f"is at line {found[0]}"
    if cited is not None:
        ordered = sorted(found, key=lambda n: (abs(n - cited), n))
        # ⛔ THE ENUMERATION IS CAPPED; THE COUNT NEVER IS (#13033 review P3).
        others = ordered[1:1 + _MAX_OTHER_CANDIDATES]
        rest = ", ".join(str(n) for n in others)
        tail = (f"; the others are {rest}" if len(ordered) - 1 <= len(others)
                else f"; the next {len(others)} are {rest}, and "
                     f"{len(ordered) - 1 - len(others)} more — too generic to "
                     f"enumerate")
        return (f"is at line {ordered[0]} (nearest of {len(found)} candidates for "
                f"this anchor{tail} — if the passage means one of those, its anchor "
                f"is not unique enough to pin it)")
    shown = ", ".join(map(str, found[:_MAX_OTHER_CANDIDATES + 1]))
    more = ("" if len(found) <= _MAX_OTHER_CANDIDATES + 1
            else f", and {len(found) - _MAX_OTHER_CANDIDATES - 1} more")
    return f"is on {len(found)} lines ({shown}{more})"
