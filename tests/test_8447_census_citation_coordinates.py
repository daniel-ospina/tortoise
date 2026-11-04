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

#: `10217`→`10866` / `10804-10813`→`11453-11462` — a retired coordinate pair: an
#: optional inclusive `from` range, the arrow that retires it, and the `to` range
#: it was retired for. **Both ends are captured**, because a "this pair retires
#: something" assertion cannot be written against the source alone: built from
#: the source only, the span is a non-empty set for every single-number entry, so
#: the assertion is unreachable (#10620 review, issue 2).
#:
#: Whitespace around the arrow is optional. The header writes the SPACED form for
#: SHAs one paragraph above the ledger (`` `63981d152` → `223301c4` ``), so a
#: re-derivation that copies that style is a live future — and while the arrow
#: was required to abut the closing backtick, such an entry was invisible, every
#: coordinate it recorded stayed live, and the coordinate check passed on a
#: document carrying the very defect it exists for (#10620 review, issue 3: a
#: fail-open on a spelling the document itself uses).
#:
#: `\d{1,6}`, not `\d+`: a ledger-shaped span of eight or more digits is a short
#: SHA — not a line number — and admitting one plants a retired coordinate no
#: citation can collide with, which is noise that never catches a defect.
_LEDGER_ENTRY = re.compile(
    r"`(\d{1,6})(?:-(\d{1,6}))?`\s*→\s*`(\d{1,6})(?:-(\d{1,6}))?`"
)

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


def _span(first: str, last: str | None) -> set[int]:
    """An inclusive coordinate span. An inverted range yields the empty set."""
    return set(range(int(first), int(last) + 1)) if last else {int(first)}


def _ledger_pairs(header: str) -> list[tuple[set[int], set[int]]]:
    """(retired span, target span) for every ``from``→``to`` pair in `header`."""
    return [
        (_span(match.group(1), match.group(2)), _span(match.group(3), match.group(4)))
        for match in _LEDGER_ENTRY.finditer(header)
    ]


def _retired_coordinates(header: str) -> set[int]:
    """Every line number the header records as having been superseded."""
    retired: set[int] = set()
    for source, _target in _ledger_pairs(header):
        retired |= source
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
    pairs = _ledger_pairs(header)
    assert pairs, (
        "the provenance header carries no `from`→`to` retirement pairs, so a"
        " re-derivation can no longer be told from a re-date — and the"
        " coordinate check in this module has nothing to check against"
    )
    # Each pair must actually retire something, and retire it for something else:
    # a pair whose target equals its source is a typo, not a derivation, and an
    # inverted range (`10813-10804`) retires nothing at all because its span is
    # empty. Both ends are needed to say either — see `_LEDGER_ENTRY`.
    for source, target in pairs:
        assert source, (
            f"a ledger pair retires an empty or inverted range: {sorted(source)}."
            " `10813-10804` is not a span — write the range in ascending order."
        )
        assert target, f"a ledger pair retires TO an empty or inverted range: {sorted(target)}"
        assert source != target, (
            "a ledger pair whose target equals its source retires nothing —"
            f" that is a typo, not a derivation: {sorted(source)} →"
            f" {sorted(target)}"
        )

    retired = _retired_coordinates(header)
    assert len(retired) >= len(pairs), (pairs, sorted(retired))


def test_the_paired_construction_retires_both_of_its_lines() -> None:
    """`§2a` cited `/v1/points` as `:10265-10266` — the route line AND its handler `def`.

    A ledger that retires `10265` and only *names* `10915` in prose leaves `10266`
    retired nowhere, so a stale `:10266` cite passes the coordinate check while
    pointing at unrelated code at the anchor head. That is the same one-construction-
    two-coordinate-systems defect this module exists for, surviving inside the ledger.

    ⚠️ Measured before this pin: rewriting both the `/v1/points` table row and §2a to
    cite `hosted_api.py:10266` passed every test in this module (`retired` held no
    `10266`). The numbers are historical constants, not a moving measurement — they
    belong to the fixed retired head `d72983b99` and its target `d3f148a56`, where
    `10265` is `@app.get("/v1/points")` and `10266` is `async def list_points(`.
    """
    header, _body = _split_document()
    sources = [source for source, _target in _ledger_pairs(header)]
    assert any({10265, 10266} <= source for source in sources), (
        "the header does not retire `/v1/points`'s full `10265-10266` extent, so a"
        " stale cite to the second half (`list_points`'s `def` line) would pass the"
        f" coordinate check: pairs={sources}"
    )


def test_no_live_citation_points_at_a_retired_coordinate() -> None:
    """The document must carry ONE coordinate system per construction.

    This is the finding: §2a cited the `/v1/points` route and the `/v1/search`
    marker pair at the coordinates the header had already recorded as retired,
    while the table rows cited the same two constructions at the current ones.

    ⚠️ When this fires on a citation that is CORRECT, the retired set is
    over-broad rather than the citation being wrong: line numbers are reusable, so
    after a file shrinks (or in a different construction) a good citation can land
    on a number some earlier re-derivation retired. That is a false positive, and
    the repair is neither to delete a ledger row — that un-retires the defect the
    row exists for, and the row is the only record of the move — nor to delete
    this test. Restate the ledger entry so it records the move this citation
    actually made, or quote the fragment in the row so the cite stops being a bare
    number. Silencing the instrument by removing its evidence is the failure
    `AGENTS.md` names; if the false positive is real, record it and change the
    mechanism deliberately.
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

    `:10914-10915` carries no filename and no quoted fragment of its own, so the
    document's own mitigation ("search the quoted fragment") cannot rescue it —
    the fragment quoted in that sentence belongs to the *correct* `:10973`. A
    bare cite in the hosted section is therefore only trustworthy when the
    document names that same coordinate EXPLICITLY somewhere else; the table row
    is what makes the §2a shorthand resolvable. That — bare ⊆ explicit — is what
    this asserts.

    (The first version asserted only that at least one bare cite and at least one
    explicit cite EXISTED, which no arrangement of the document could falsify: it
    stayed green with §2a citing `:10266-10267`, a coordinate named nowhere.
    #10620 review, issue 4.)
    """
    _header, body = _split_document()
    citations = _live_citations(body)
    assert citations, "no hosted_api.py citations were found — the parser drifted"

    bare_coordinates = {
        coordinate
        for _line, cite, coordinates in citations
        if cite.startswith("`:")
        for coordinate in coordinates
    }
    assert bare_coordinates, (
        "§2a's hosted cites are bare `:NNN` continuations; finding none means"
        " either the parser or the document shape changed"
    )
    explicit_coordinates = {
        coordinate
        for _line, cite, coordinates in citations
        if not cite.startswith("`:")
        for coordinate in coordinates
    }
    assert explicit_coordinates, "no explicit `hosted_api.py:NNN` cite to anchor the bare ones"

    unattributed = bare_coordinates - explicit_coordinates
    assert not unattributed, (
        f"§2a cites {sorted(unattributed)} as bare `:NNN`, but the document never"
        " names that coordinate explicitly anywhere. A bare cite carries no"
        " filename and no fragment, so it is only resolvable through an explicit"
        " `hosted_api.py:NNN` — cite the coordinate in the table row, or drop it"
        " from §2a, rather than leaving a number that resolves to nothing."
    )


# ---------------------------------------------------------------------------
# Non-vacuity: each guard above, driven with the mutation it names.
#
# A guard over a document reads its own expectations out of that document, so
# the only honest way to show it can fail is to hand the SAME predicates the
# mutated input. Each test below is the mutation a reviewer used to disprove the
# first version of these checks (#10620 review, issues 2-4); they run in-process
# against `_ledger_pairs` / `_retired_coordinates` / `_live_citations` rather
# than against a `/tmp` copy of the document, so they cannot rot silently.
# ---------------------------------------------------------------------------


def test_a_ledger_pair_that_retires_nothing_is_rejected() -> None:
    """`test_the_header_ledger_is_a_ledger`'s per-pair checks, on mutations.

    The disproved version built its span from the SOURCE alone, so ``assert
    span`` was a non-empty set for every single-number entry and could not fail.
    These are the two shapes it was meant to catch, asserted against the same
    predicates the real test now uses.
    """
    self_retiring = "`10265`→`10265`"
    pairs = _ledger_pairs(self_retiring)
    assert pairs == [({10265}, {10265})], (
        "the ledger parser must capture BOTH ends of a pair — with only the"
        f" source captured, a self-retiring pair is indistinguishable from a"
        f" real one: {pairs}"
    )
    source, target = pairs[0]
    assert source == target, "a self-retiring pair must compare equal, so the real test rejects it"

    inverted = "`10813-10804`→`11453-11462`"
    inverted_source, _inverted_target = _ledger_pairs(inverted)[0]
    assert not inverted_source, (
        "an inverted range must retire nothing, so the real test's `assert"
        f" source` rejects it: {sorted(inverted_source)}"
    )


def test_a_spaced_ledger_entry_retires_the_same_coordinates() -> None:
    """The ledger's arrow has two spellings in the wild; both must parse.

    The header writes the SPACED form for SHAs one paragraph above the ledger
    (`` `63981d152` → `223301c4` ``), so a re-derivation that copies that style is
    a live future. The disproved version required the arrow to abut the closing
    backtick: the entry became invisible, every coordinate it recorded stayed
    live, and the coordinate check passed on a document carrying the defect.
    "Some entry parses" would not catch that — the two spellings must retire the
    SAME set.
    """
    tight = "`10804-10813`→`11453-11462`"
    spaced = "`10804-10813` → `11453-11462`"
    assert _LEDGER_ENTRY.search(tight), "the tight spelling must parse"
    assert _LEDGER_ENTRY.search(spaced), (
        "a ledger entry written with spaces around its arrow did not parse — the"
        " coordinates it retires stay live and the coordinate check fails OPEN"
    )
    expected = set(range(10804, 10814))
    assert _retired_coordinates(tight) == expected
    assert _retired_coordinates(spaced) == expected, (
        "the spaced spelling must retire exactly the same coordinates as the"
        f" tight one: got {sorted(_retired_coordinates(spaced))}"
    )

    # A short SHA whose hex happens to be all digits is not a line number.
    assert not _LEDGER_ENTRY.search("`12345678` → `87654321`"), (
        "an eight-digit SHA must not be admitted as a retired coordinate"
    )


def test_a_bare_cite_named_nowhere_is_unattributed() -> None:
    """`test_a_bare_cite_is_attributed_to_the_named_file`, on a mutated body.

    Narrows the table row's explicit `/v1/points` cite to the decorator alone —
    which is what the row said before this test existed — so §2a's bare
    `:10914-10915` has one coordinate the document names nowhere. The
    attribution check must see it; the disproved version (existence only) did not.
    """
    row_cite = "`hosted_api.py:10914-10915`"
    _header, body = _split_document()
    assert row_cite in body, (
        f"the table row no longer carries {row_cite} — update this mutation so it"
        " narrows the cite the document actually writes"
    )
    mutated = body.replace(row_cite, "`hosted_api.py:10914`", 1)

    bare = {
        coordinate
        for _line, cite, coordinates in _live_citations(mutated)
        if cite.startswith("`:")
        for coordinate in coordinates
    }
    explicit = {
        coordinate
        for _line, cite, coordinates in _live_citations(mutated)
        if not cite.startswith("`:")
        for coordinate in coordinates
    }
    assert bare - explicit == {10915}, (
        "narrowing the row's explicit cite must leave §2a's `10915`"
        f" unattributed; it left {sorted(bare - explicit)} — if this is empty,"
        " the attribution check is vacuous again"
    )
