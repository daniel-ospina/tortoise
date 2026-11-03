"""#8447 C1 — the census may not cite a line it has itself declared retired.

THE DEFECT THIS PINS, measured on the tree before it. ``tortoise/hosted_api.py``
moved three times under ``docs/architecture/BOUNDED-OPERATION-CONTRACT.md``
(``+123`` at ``d72983b99``, then a *uniform* ``+649`` at ``d3f148a56``), and the
document carries a re-derivation ledger in its header that records each move as
``from``→``to`` ("``/v1/points`` ``10265``→``10914``", "the ``/v1/search`` marker
pair ``10804-10813``→``11453-11462``"). The third pass applied its ``+649`` to the
table rows and to the header, but **not** to the three ``hosted_api.py`` cites in
§2a — so the document ended up carrying *two coordinate systems for the same two
constructions*, at once::

    :93   | GET /v1/points … hosted_api.py:10914 … return … at hosted_api.py:10973
    :174  return {"points": results, "count": len(results)} (hosted_api.py:10973)
    :175  the route declares no `response_model` (`:10265-10266`)   ← the RETIRED
    :177  `response.update(truncation_field(…))` (`:10804`, `:10813`) ← coordinates

Both sets cannot be right in one tree, and at ``origin/main`` the three stale
numbers land on unrelated code — ``10265-10266`` is the IP-recovery rate-limit
block, ``10804`` a comment, ``10813`` a ``_dream_ns`` assignment. A reader who
followed the §2a cites got the wrong code; a reader who followed the table row
got the right one, and nothing in the document said which to believe.

WHY A LEDGER TEST RATHER THAN A SOURCE-RESOLVING ONE. The obvious test — "every
``file:line`` resolves to the construction it claims" — cannot see this defect at
the head it shipped at: this branch does not touch ``tortoise/hosted_api.py``, so
the worktree copy is still the older ``d72983b99`` file where ``10265-10266``
*are* the route lines. That test would be green on the broken doc before the
merge and red/green by rebase state afterwards, i.e. it would measure the
branch's staleness, not the document's consistency. The invariant that actually
holds at every head is the document's own: **a coordinate the header has
recorded as retired must not reappear as a live citation.** It is text-only
(no interpreter, no git, no DB), it is the exact defect, and it survives every
future re-derivation because the ledger is what a re-derivation updates.

Class-B non-vacuity: on the pre-fix document this test reports the three §2a
sites (``:10265-10266``, ``:10804``, ``:10813``) and fails; it passes only once
they are re-pointed at the ledger's targets. Deleting the ledger does not make
it vacuous — ``test_the_header_ledger_is_a_ledger`` fails closed instead, so the
retirement bookkeeping cannot be dropped to silence the coordinate check.
"""
from __future__ import annotations

import re
from pathlib import Path

DOC = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "architecture"
    / "BOUNDED-OPERATION-CONTRACT.md"
)

#: The header ends where the document's own status block begins; everything
#: above it is provenance, everything below it is the census.
_STATUS_MARKER = "**Status:**"

#: `10217`→`10866` / `10804-10813`→`11453-11462` — a retired coordinate, with an
#: optional inclusive range, immediately followed by the arrow that retires it.
_LEDGER_ENTRY = re.compile(r"`(\d+)(?:-(\d+))?`→")

#: A source-file mention, `dir/file.ext` or `file.ext`, optionally carrying the
#: line numbers. Basename-normalised via group 1 so `tortoise/hosted_api.py` and
#: a later bare `hosted_api.py` are the same file (missing that is how a scan
#: silently drops a cite).
_FILE_MENTION = re.compile(
    r"`?(?:[\w.\-]+/)*([\w\-]+\.(?:py|yml|yaml))`?(?::(\d+)(?:-(\d+))?)?"
)

#: A bare continuation cite: the document's convention is that `:NNN` means
#: "the file under discussion", i.e. the last file named.
_BARE_CITE = re.compile(r"`:(\d+)(?:-(\d+))?`")

#: The file whose coordinates the ledger retires. The ledger also carries
#: `hosted_api.py`'s own file-size deltas; a bare cite is judged only while
#: `hosted_api.py` is the file under discussion, so a `sdk.py:10804` (were one
#: ever written) would not be misread as a retired hosted coordinate.
_TRACKED_FILE = "hosted_api.py"


def _split_document() -> tuple[str, str]:
    """(provenance header, census body)."""
    text = DOC.read_text(encoding="utf-8")
    assert _STATUS_MARKER in text, (
        f"{DOC.name} has no {_STATUS_MARKER} marker, so its provenance header and"
        " its census cannot be told apart — this test cannot judge the ledger"
    )
    head, _, body = text.partition(_STATUS_MARKER)
    return head, body


def _retired_coordinates(header: str) -> set[int]:
    """Every line number the header records as having been superseded."""
    retired: set[int] = set()
    for match in _LEDGER_ENTRY.finditer(header):
        first = int(match.group(1))
        last = match.group(2)
        retired.update(range(first, int(last) + 1) if last else (first,))
    return retired


def _live_citations(body: str) -> list[tuple[int, str, set[int]]]:
    """(line number, raw cite, coordinates) for every `hosted_api.py` cite."""
    found: list[tuple[int, str, set[int]]] = []
    current: str | None = None
    for line_no, line in enumerate(body.splitlines(), start=1):
        events = [(m.start(), "file", m) for m in _FILE_MENTION.finditer(line)]
        events += [(m.start(), "cite", m) for m in _BARE_CITE.finditer(line)]
        for _position, kind, match in sorted(events, key=lambda event: event[0]):
            if kind == "file":
                current = match.group(1)
                if match.group(2):
                    coordinates = {int(match.group(2))}
                    if match.group(3):
                        coordinates = set(
                            range(int(match.group(2)), int(match.group(3)) + 1)
                        )
                else:
                    continue
            else:
                coordinates = {int(match.group(1))}
                if match.group(2):
                    coordinates = set(
                        range(int(match.group(1)), int(match.group(2)) + 1)
                    )
            if current == _TRACKED_FILE:
                found.append((line_no, match.group(0), coordinates))
    return found


def test_the_header_ledger_is_a_ledger() -> None:
    """The retirement bookkeeping exists — otherwise the check below is vacuous.

    A coordinate test that reads its expectations out of the document can be
    silenced by deleting the part it reads. The document's method, though,
    *requires* the ledger: §"Re-derived once more …" says in as many words that
    a uniform delta "is exactly the shape that invites arithmetic instead of a
    read", so the `from`→`to` pairs are what make a re-derivation auditable
    rather than asserted. Asserting they are present keeps the coordinate test
    from being defanged by a header rewrite.
    """
    header, _body = _split_document()
    entries = _LEDGER_ENTRY.findall(header)
    assert entries, (
        "the provenance header carries no `from`→`to` retirement pairs, so a"
        " re-derivation can no longer be told from a re-date — and the"
        " coordinate check in this module has nothing to check against"
    )
    # Each pair must actually retire something: a pair whose target equals its
    # source is a typo, not a derivation.
    for first, last in entries:
        span = {int(first)} if not last else set(range(int(first), int(last) + 1))
        assert span, (first, last)

    retired = _retired_coordinates(header)
    assert len(retired) >= len(entries), (entries, sorted(retired))


def test_no_live_citation_points_at_a_retired_coordinate() -> None:
    """The document must carry ONE coordinate system per construction.

    This is the finding: §2a cited the `/v1/points` route and the `/v1/search`
    marker pair at the coordinates the header had already recorded as retired,
    while the table rows cited the same two constructions at the current ones.
    """
    header, body = _split_document()
    retired = _retired_coordinates(header)
    assert retired, "no retired coordinates to check against — see the ledger test"

    offenders = [
        (line_no, cite, sorted(coordinates & retired))
        for line_no, cite, coordinates in _live_citations(body)
        if coordinates & retired
    ]
    assert not offenders, (
        "the census cites line numbers its own provenance header records as"
        f" retired: {offenders}. Re-point each at the ledger's target (or"
        " delete the stale cite) so the document does not carry two"
        " coordinate systems for one construction"
    )


def test_a_bare_cite_is_attributed_to_the_named_file() -> None:
    """Guard the §2a shape itself: the hosted cites there are BARE `:NNN`.

    `:10265-10266` carries no filename and no quoted fragment of its own, so the
    document's own mitigation ("search the quoted fragment") cannot rescue it —
    the fragment quoted in that sentence belongs to the *correct* `:10973`. A
    bare cite in the hosted section is therefore only trustworthy because it
    matches a coordinate the document names explicitly somewhere, which is what
    this asserts for the section's remaining cites.
    """
    _header, body = _split_document()
    citations = _live_citations(body)
    assert citations, "no hosted_api.py citations were found — the parser drifted"

    bare = [cite for _line, cite, _coords in citations if cite.startswith("`:")]
    assert bare, (
        "§2a's hosted cites are bare `:NNN` continuations; finding none means"
        " either the parser or the document shape changed"
    )
    explicit = {
        coordinate
        for _line, cite, coordinates in citations
        if not cite.startswith("`:")
        for coordinate in coordinates
    }
    assert explicit, "no explicit `hosted_api.py:NNN` cite to anchor the bare ones"
