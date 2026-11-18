"""#12184 — every evidence cite this branch's own inserts moved must land on the line it names.

THE DEFECT THIS PINS
--------------------
Two inserts on this branch shift every `file.py:NNNN` citation BELOW them:

* `tortoise/search_engine.py` — `run_fts_query`'s `_searchText`-per-door docstring, **+3** at `:2842`
  (`git diff origin/main...HEAD --unified=0` -> `@@ -2842 +2842,4 @@`);
* `tortoise/cache_projection.py` — the `_searchText` comment rewrite, **+2** at `:2128` (`@@ -2128,3
  +2128,5 @@`).

The documents below are EVIDENCE: a reader re-verifies a passage by opening the line it cites, so a
cite that is one line off reads exactly like a correct one and silently discounts the passage.

⚠️ The failure this file was written for was not a stale number, it was a **FALSE CORRECTION**: the
re-derivation pass at this branch's tip re-pointed
`docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`'s `for rank, (pid, _score) in
enumerate(ranked)` cite from `:4549` to `:4552` — applying the `+3` a SECOND time to a number that
already carried it. The loop read `:4546` on `origin/main` (where the doc's cite was right), moved to
`:4549` with the `+3` above, was corrected `:4546` -> `:4549` at `18d18b3565` — and `:4552` is the
`#7704` note UNDER that loop. A `⚠️ CORRECTED` marker records that the text changed, never that the
change was right, so each cite is pinned to its CONTENT here, never to a delta someone added.

WHAT MAKES EACH ROW FAIL, AND WHY THAT IS REACHABLE
---------------------------------------------------
A row is `(document, cite token, target file, 1-based line the document names, verbatim fragment)`.
It reds in two reachable states: the document no longer carries the cite token (the row was re-pointed
or dropped), or the named line does not contain the fragment the document quotes for it — which is the
shipped pre-fix state of FOUR rows:

* `research-brief.md` `:4552` (x2 rows — the table row and the prose cite) — the fix is `:4549`;
* `SUBSTRATE-REBUILD-MAP.md` `:3392` and its block-open `:3388` — the fix is `:3393` / `:3391`.

⚠️ Two conventions keep a row honest, so neither is a copy of the document's rendering:

* a RANGE cite (`4425-4429`, `4353-4355`, `2650-2657`, …) is anchored on the line inside the range
  that carries the fragment the document actually quotes for it, not blindly on the range's first line;
* where the document renders a template (`MATCH (n:{label})`) the fragment is the LITERAL on the line
  (`MATCH (n:{label_str})`), because the rendered form appears in no file.

The negative-control tests below are the other direction: they mutate a COPY of a shipped row (a
spurious `+3` with the cite token left intact, and a dropped cite) and assert the resolver reports it. A
guard whose negative case cannot be reached proves the code ran, not that the tree agrees with the
document.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_AUDIT = "docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md"
_SUBSTRATE = "docs/architecture/SUBSTRATE-REBUILD-MAP.md"
_M3_BRIEF = "docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md"
_CENSUS = "docs/evidence/13083-capability-census/README.md"
_EVIDENCE = "docs/architecture/ENTITY-VERDICT-BANDS.md"
_SE = "tortoise/search_engine.py"
_CP = "tortoise/cache_projection.py"
_AU = "tortoise/audit.py"
_PI = "tortoise/projection/__init__.py"
_SDK = "tortoise/sdk.py"
_MCP = "tortoise/mcp_server.py"
_RS = "tortoise/record_serve.py"

#: (document, cite token the document must still carry, target file, 1-based line, fragment at that line)
_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # --- docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md (search_engine.py +3, cache_projection.py +2)
    (_AUDIT, "cache_projection.py:3735", _CP, 3735, 'if table == "point"'),
    (_AUDIT, "search_engine.py:4780-4784", _SE, 4784, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)"),
    (_AUDIT, "search_engine.py:6667", _SE, 6667, "(op)-[r2:IMPL|hasPart]-(other:Point)"),
    (_AUDIT, "search_engine.py:7026", _SE, 7026, "(n:Point)-[r:aboutSubject]->(:Subject)"),
    (_AUDIT, "search_engine.py:7028", _SE, 7028, "(m:Event)-[r2:aboutSubject]->(:Subject)"),
    # Row 149's TWO BARE range cites carry no `search_engine.py` prefix, so the token-level pins for
    # `6659`/`6661` above do not reach them — they are the only shifted mentions in this document the
    # first pin set missed. Both were already correct at the pre-fix head, so these two are
    # COMPLETENESS pins: they catch the re-point/double-shift that DID happen in this class (`:4549`
    # re-pointed to `:4552`), which is the failure the whole file exists for.
    (_AUDIT, "`:7025-7028`", _SE, 7025, "_SUBJECT_SOURCE_SCOPED_PROBE = ("),
    (_AUDIT, "`:7031-7044`", _SE, 7031, "def subject_binding_available"),
    (_AUDIT, "search_engine.py:3098", _SE, 3098, "CALL db.idx.fulltext.queryNodes"),
    (_AUDIT, "| `tortoise/search_engine.py:4708-4710` |", _SE, 4708, "MATCH (n:{label_str})"),
    # ⚠️ The token above carries its TABLE context on purpose. A bare `search_engine.py:4708-4710` is a
    # SUBSTRING of §2.1's verdict token (`` `none`: `search_engine.py:4708-4710` ``), so the verdict row
    # satisfies this one and the TABLE row can be re-pointed alone without reddening anything — measured:
    # mutate the table to `:4511-4513` with the bare token -> 0 red, with the table-context token -> 1 red.
    (_AUDIT, "search_engine.py:4123", _SE, 4123, "CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit"),
    (_AUDIT, "search_engine.py:3003", _SE, 3003, 'label = "Source" if entity_type == "document"'),
    (_AUDIT, "`:3070`", _SE, 3070, "the fulltext index is on Source._searchText"),
    (_AUDIT, "cache_projection.py:2653-2660", _CP, 2653, "it is the ONLY field the Source FTS index"),
    (_AUDIT, "cache_projection.py:2663-2665", _CP, 2663, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    # Row 387's bare range, quoting "un-indexes a restored session". Its sibling pins (`2660-2662`,
    # `2650-2657`) are different ranges and do not reach this one. NON-VACUOUS: the pre-fix document
    # carried `:2648-2649` here, so the cite-token arm fires on that tree (measured).
    (_AUDIT, "`:2653-2654`", _CP, 2654, "un-indexes a restored session"),
    (_AUDIT, "cache_projection.py:2715", _CP, 2715, "tail_denied = forbidden | {"),
    # --- the rest of the header's enumerated `main` set, pinned by CONTENT like every other row.
    # `_MAIN_SET_MISSED` below pins the EIGHT the header was missing; these are the entries the header
    # already named. Without them the enumeration is a promise: `#13290` moved every `search_engine.py`
    # entry in it and NOTHING red — the document went on naming held addresses over a tree that had
    # moved them, and the reader rule sent the reader to the wrong line. A pin per entry is what turns
    # the list into a guard.
    (_AUDIT, "`:2876`", _SDK, 2876, "SET s.capture_redactions=$redactions"),
    (_AUDIT, "`sdk.py:36985`", _SDK, 36985, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    (_AUDIT, "`:37008`", _SDK, 37008, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "`audit.py:817`", _AU, 817, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2452`", _AU, 2452, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2463`", _AU, 2463, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`mcp_server.py:5089`", _MCP, 5089, "entity_type=entity_type"),
    (_AUDIT, "`:5529`", _MCP, 5529, "entity_type=entity_type"),
    (_AUDIT, "`:8034`", _MCP, 8034, "entity_type=entity_type"),
    (_AUDIT, "`:6854-6870`", _PI, 6854, "_FULLTEXT_INDEX_LABEL_FIELDS"),
    (_AUDIT, "`:6864`", _PI, 6864, '("Event", ["subject", "name"])'),
    (_AUDIT, "`:6865`", _PI, 6865, '("Subject", ["name"])'),
    (_AUDIT, "`:6870`", _PI, 6870, '("Source", ["_searchText"])'),
    (_AUDIT, "record_serve.py:461", _RS, 461, "READ_POINT_BY_ID: AnchoredRead("),
    # --- docs/architecture/SUBSTRATE-REBUILD-MAP.md §5's move list (the #11682 corpus-join block)
    (_SUBSTRATE, "tortoise/search_engine.py:3449", _SE, 3449, "``:Source``/``:Document`` row must not contribute"),
    (_SUBSTRATE, "`:3447`", _SE, 3447, "# #11682: the corpus join is PER FAMILY"),
    # --- docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md
    (_M3_BRIEF, "tortoise/search_engine.py:2886", _SE, 2886, "def run_fts_query"),
    (_M3_BRIEF, "search_engine.py:2728", _SE, 2728, "def recency_tie_clause"),
    (_M3_BRIEF, "tortoise/search_engine.py:4904", _SE, 4904, "for rank, (pid, _score) in enumerate(ranked)"),
    (_M3_BRIEF, "`search_engine.py:4823`", _SE, 4823, "def rrf_fusion("),
    # --- docs/architecture/ENTITY-VERDICT-BANDS.md — the D-1 row "the retrieval filter is status-only"
    # ⚠️ This row is a SECOND false correction, of the same class this file exists for. The document
    # cited `search_engine.py:2973-2997`; other PRs' inserts had already moved the status filter to
    # `:3020` on `origin/main`, so the cite was ~44 lines stale BEFORE this branch's `+3`. Adding the
    # `+3` (`2973-2997` -> `2976-3000`) therefore preserved the staleness while reading as a correction,
    # and `2976-3000` is the `build_or_query` byte-identity comment block, not the filter. The pin below
    # is anchored on the line that IS the filter, so the next offset-only edit reds instead of shipping.
    (_EVIDENCE, "search_engine.py:3079-3094", _SE, 3079, "if label in _STATUS_FILTERED_LABELS"),
    # --- docs/evidence/13083-capability-census/README.md
    (_CENSUS, "cache_projection.py:2715", _CP, 2715, "tail_denied = forbidden | {"),
)

#: The `main`-set group the `#13124` review found OUTSIDE the header's `main` enumeration: (token the
#: header must carry, target file, 1-based line at `main`, verbatim fragment on that line). Five of these
#: are reached by `_PINS` already; `:2433`, `:2490` and `projection/entities.py:5646` were in NO pin at all
#: before this one, and the header named none of the eight — which is the defect
#: `test_the_main_set_enumeration_names_the_group_it_was_missing` exists for.
_ENT = "tortoise/projection/entities.py"
_MAIN_SET_MISSED: tuple[tuple[str, str, int, str], ...] = (
    (":2433", _CP, 2433, "def _episodic_source_search_text_is_the_lexicon"),
    (":2490", _CP, 2490, 'return summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"'),
    (":2653-2660", _CP, 2653, "it is the ONLY field the Source FTS index"),
    (":2653-2654", _CP, 2654, "un-indexes a restored session"),
    (":2663-2665", _CP, 2663, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    (":2715", _CP, 2715, "tail_denied = forbidden | {"),
    (":3735", _CP, 3735, 'if table == "point"'),
    (":5646", _ENT, 5646, "s.summary=coalesce($summary, s.summary, '')"),
)


#: How many times each pinned token must appear in its document. `_resolve`'s token arm is an EXISTENCE
#: test over the whole file, so a token occurring twice survives a re-point at ONE site: the sibling
#: keeps the arm green, and the content arm still passes because it opens the TARGET file, which such an
#: edit does not touch. Measured on the row this test was written for — `cache_projection.py:3735` occurs
#: 3x, and re-pointing one occurrence to `:3738` red 0 of 7 tests. Counts make every SITE load-bearing
#: instead of every STRING; the `#13083` review that found it named 15 shared tokens, which is why this
#: is a set of counts and not another context-bearing token.
_TOKEN_OCCURRENCES: dict[tuple[str, str], int] = {
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:3735'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4780-4784'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:6667'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7026'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7028'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7025-7028`'): 4,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7031-7044`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3098'): 5,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '| `tortoise/search_engine.py:4708-4710` |'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4123'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3003'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3070`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2653-2660'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2663-2665'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2653-2654`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2715'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2876`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:36985`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:37008`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`audit.py:817`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2452`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2463`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`mcp_server.py:5089`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5529`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:8034`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6854-6870`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6864`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6865`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6870`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'record_serve.py:461'): 3,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', 'tortoise/search_engine.py:3449'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:3447`'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:2886'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'search_engine.py:2728'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:4904'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', '`search_engine.py:4823`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'search_engine.py:3079-3094'): 1,
    ('docs/evidence/13083-capability-census/README.md', 'cache_projection.py:2715'): 1,
}


def test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence() -> None:
    """A re-pointed SITE must red even when the same string occurs elsewhere in the document."""
    texts: dict[str, str] = {}
    for doc, token, _path, _line, _fragment in _PINS:
        key = (doc, token)
        assert key in _TOKEN_OCCURRENCES, (
            f"{token!r} in {doc} has no declared occurrence count — a new pin row needs one, or a "
            "re-point at one of its sites is invisible to this file."
        )
        if doc not in texts:
            texts[doc] = (REPO / doc).read_text(encoding="utf-8")
        actual = texts[doc].count(token)
        assert actual == _TOKEN_OCCURRENCES[key], (
            f"{doc} carries `{token}` {actual}x, not {_TOKEN_OCCURRENCES[key]}x — one SITE was "
            "re-pointed while a sibling kept the string, which is how the label-scan table row was "
            "re-pointed with nothing going red. Re-derive the site by CONTENT and update the count to "
            "match, rather than deleting the occurrence."
        )
    declared = set(_TOKEN_OCCURRENCES)
    pinned = {(doc, token) for doc, token, _p, _l, _f in _PINS}
    assert declared == pinned, (
        f"_TOKEN_OCCURRENCES and _PINS disagree: {sorted(declared - pinned)} is declared with no pin; "
        f"{sorted(pinned - declared)} is pinned with no count."
    )


def test_the_measured_figures_and_the_pin_side_descriptions_are_the_re_measured_ones() -> None:
    """REVERT GUARD, not a re-measurement — say what this does NOT prove.

    The drift figures and the two pin-side descriptions below were each corrected by the `#13083` review:
    the counts still read `322`/`216`/`106` for a file that measures `314`/`211`/`103`, and the pin-side
    prose described `:3735` as the `MERGE … SET n += $props` (that statement is the pin's `:3732`; `:3735`
    is `for prop in sorted(STORED_VECTOR_PROPS):`) and `:2715` as `placement_spellings()` (that function is
    the pin's `:1798`; `:2715` is `if k not in tail_denied})`). Nothing asserted them, so both could revert
    silently — that is what this test stops.

    ⛔ It does NOT prove the figures are CURRENT: a figure goes stale the moment this file gains or loses a
    cite, and this test would happily pass over a newly-wrong number. Re-measuring is the header's own
    instruction, and the rule it states is reproducible with `git show <head>:<path> | sed -n '<N>p'`.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    for claimed in (
        "124 of this document's 211",
        "dedupes the 314 as-written spellings to 211 — 103",
        "the pin's `:3735` is",
        "for prop in sorted(STORED_VECTOR_PROPS):",
    ):
        assert claimed in text, (
            f"the header no longer carries {claimed!r}. If the FILE changed, re-measure the figures by the "
            "rule the header states and update both the claim and its cross-reference; do not restore an "
            "older number."
        )
    for falsified in (
        "`placement_spellings()` at the pin",
        "`MERGE (n:…) SET n += $props` at the pin",
    ):
        assert falsified not in text, (
            f"the header again describes an address by content it does not carry at the pin ({falsified!r}) "
            "— open the pin's line before describing it."
        )


def _resolve(pins, repo: Path = REPO) -> list[str]:
    """Every reason the *pins* do not hold at *repo*'s tree (empty list = they all hold)."""
    errors: list[str] = []
    for doc_rel, token, target_rel, line, fragment in pins:
        doc_text = (repo / doc_rel).read_text(encoding="utf-8")
        if token not in doc_text:
            errors.append(
                f"{doc_rel}: the citation `{token}` is gone — the row was re-pointed or dropped. "
                "Re-derive it against the head file rather than deleting the pin."
            )
            continue
        target_lines = (repo / target_rel).read_text(encoding="utf-8").splitlines()
        if not 0 < line <= len(target_lines):
            errors.append(
                f"{doc_rel}: `{target_rel}:{line}` is out of range "
                f"({len(target_lines)} lines) — the file shrank under the cite."
            )
            continue
        actual = target_lines[line - 1]
        if fragment not in actual:
            errors.append(
                f"{doc_rel} cites `{target_rel}:{line}` for {fragment!r}, but that line reads "
                f"{actual.strip()[:100]!r} — the cite rotted, or an offset was applied twice. "
                f"RE-DERIVE BY CONTENT: grep -n {fragment!r} {target_rel}"
            )
    return errors


def test_every_pinned_cite_lands_on_the_content_the_document_quotes() -> None:
    """Fails on the pre-fix tree: the m3 brief names `:4552` and §5 names `:3392`/`:3388`."""
    errors = _resolve(_PINS)
    assert not errors, "\n".join(errors)


def test_the_resolver_reds_on_a_double_applied_offset() -> None:
    """Negative control — the exact defect: 4904 + 3 = 4907 is the `#7704` note, not the loop.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).
    """
    row = (_M3_BRIEF, "tortoise/search_engine.py:4904", _SE, 4904, "for rank, (pid, _score) in enumerate(ranked)")
    doubled = (row[0], row[1], row[2], 4907, row[4])
    errors = _resolve((doubled,))
    assert errors, "a spurious +3 must red the resolver"
    assert ":4907" in errors[0] and "#7704" in errors[0]
    assert "is gone" not in errors[0], "the content arm must be the one that fires"


def test_the_resolver_reds_when_the_document_no_longer_carries_the_cite() -> None:
    """Negative control for the other arm: a dropped/re-pointed cite cannot pass by being absent."""
    row = (_M3_BRIEF, "search_engine.py:99999", _SE, 4549, "for rank, (pid, _score) in enumerate(ranked)")
    errors = _resolve((row,))
    assert errors, "a cite the document does not carry must red the resolver"
    assert "is gone" in errors[0]


def test_the_pinned_documents_are_the_ones_this_diff_shifted() -> None:
    """The pin set is bounded to the documents the review named — it must not silently shrink.

    Reachable: a row deleted to make the suite pass is the failure mode; `grep -c '^def test_'` and this
    count both move together only when a row is added on purpose.
    """
    docs = {row[0] for row in _PINS}
    assert docs == {_AUDIT, _SUBSTRATE, _M3_BRIEF, _CENSUS, _EVIDENCE}
    assert len(_PINS) == 38


def test_the_recorded_wrong_addresses_are_never_delta_shifted() -> None:
    """The sentence that records WHICH addresses were wrong is HISTORY, not a citation.

    `#13290` moved the recorded wrong addresses (`:3064`, `:4477-4481`, `:6352`) by its own line delta
    (`:3084`, `:4674-4678`, `:6549`) while leaving the clause BENEATH them saying `:3064`, `:4477`,
    `:6352` — the paragraph contradicted itself, and the history it exists to preserve was rewritten to
    describe lines that nobody ever cited.

    `_PINS` cannot see this: there is no line to open, because the sentence is ABOUT addresses that were
    wrong. A token assertion is the only shape available, which is why this is a test and not a row.

    NON-VACUOUS: the delta-shifted form is a shipped state —
    `git show 337cba88:docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | grep -c ':3084'` -> 1 — so both
    assertions red on that tree.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    assert "(`:3064`, `:4477-4481`, `:6352`)" in text, (
        "the paragraph that records which addresses were WRONG no longer names `:3064`, `:4477-4481`, "
        "`:6352` — it is history, so re-deriving it by content is not possible and a delta applied to it "
        "is the defect it warns about, one level up."
    )
    for shifted in (":3084", ":4674-4678", ":6549"):
        assert shifted not in text, (
            f"the historical wrong-address list carries `{shifted}`, a line delta applied to an address "
            "that was never right — the addresses the census actually wrote were `:3064`, `:4477-4481` "
            "and `:6352`, and the clause beneath them still says so."
        )


def test_the_section_2_1_verdict_names_the_same_label_scan_line_as_its_table() -> None:
    """One reader, ONE address — the census names the label scan in three places.

    The `_PINS` mechanism could not catch this defect: the pin row for the label scan
    (`search_engine.py:4511-4513`) was satisfied by the §2.1 TABLE, which already carried the moved
    address, so a VERDICT paragraph left behind at the pre-move address red no pin while the document
    gave TWO addresses for one reader — and only one of them can open at any head. The `#13124`
    post-merge review found exactly that (`:4350-4351` in the verdict, `:4511-4513` in the table).

    NON-VACUOUS: the pre-fix document carries the second spelling
    (`git show origin/main:docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md |
    grep -c '`none`: `search_engine.py:4350-4351`'` -> 1), so the second assertion reds on that tree.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    assert "`none`: `search_engine.py:4708-4710`, a plain label scan" in text, (
        "§2.1's verdict paragraph no longer names the label scan at `:4708-4710`; re-derive it from "
        "the statement rather than applying a delta to the address beside it."
    )
    assert "`none`: `search_engine.py:4350-4351`" not in text, (
        "§2.1's verdict paragraph names the label scan at `:4350-4351` while the table above it names "
        "the same reader row at `:4708-4710` — two addresses for one reader, and only one of them can "
        "open at any head. That is the defect class this file exists for."
    )


def test_the_main_set_enumeration_names_the_group_it_was_missing() -> None:
    """The header's reader rule was an ABSOLUTE, and the absolute was false.

    `#13124`'s header said the enumeration was "the WHOLE of the `main` set" and that every other
    `file:line` "is at the pin `ccb49741b2ec`". The `#13124` REVIEW measured both false: eight addresses
    open at `main` and are named in neither list — `cache_projection.py` `:2433`, `:2490`, `:2653-2660`,
    `:2653-2654`, `:2663-2665`, `:2715`, `:3735` and `projection/entities.py`:5646. `:3735` is a
    `MERGE (n:…) SET n += $props` at the pin and the `cache_tier` arm at `main`, so a reader who followed
    the rule opened the pin and landed on a statement their row does not quote: "cite rotated, reader
    confidently opens the wrong line" — this document's own failure class, inverted.

    Two arms, because the defect was two-sided:

    * CONTENT — each of the eight carries, at `main` (the working tree: this branch is docs-only, so
      `main`'s `.py` files are this tree's), the fragment the document quotes for it. `_PINS` reaches
      five of them; `:2433`, `:2490` and `projection/entities.py:5646` were in NO pin before this one.
    * CLAIM — the header still NAMES them, and no longer states the three absolutes the review measured
      false, nor the "pin-written remainder" label whose denominator contradicted it.

    NON-VACUOUS on the pre-fix document (`git show 295c26ebc8:<the file>`): the header region — every
    line before `## 1.` — carries **0** occurrences of `cache_projection` and **1** of each of the three
    absolutes, so every assertion below reds there. The region bound is what makes the token arm
    discriminating: `:2433`, `:2490` and `:2715` DO appear later in the document (row 13, §2.5), so an
    unbounded `in text` check would pass on the broken file.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    header = text.split("## 1. The population", 1)[0]

    rows = tuple((_AUDIT, token, path, line, fragment) for token, path, line, fragment in _MAIN_SET_MISSED)
    errors = _resolve(rows)
    assert not errors, "\n".join(errors)

    assert "cache_projection.py" in header, (
        "the header's `main` set names no `cache_projection.py` address, but that module carries eight "
        "cites in this document that open at `main` and NOT at the pin (the pin's `:3735` is "
        "`for prop in sorted(STORED_VECTOR_PROPS):`; the `MERGE … SET n += $props` this document "
        "quotes is the pin's `:3732`). An enumeration that omits them is not the set it claims to be."
    )
    for token, _path, _line, _fragment in _MAIN_SET_MISSED:
        assert f"`{token}`" in header, (
            f"the header's `main` set does not name `{token}`, yet that address opens at `main` and not "
            "at the pin. Add it to the enumeration with its head instead of widening the claim."
        )

    for stale, why in (
        ("the WHOLE of the `main` set", "eight addresses open at `main` and sat outside it"),
        ("pin-written remainder", "its own population counts the `main`-set addresses too"),
    ):
        assert stale not in text, (
            f"the header still claims {stale!r}, which the `#13124` review measured false ({why}). "
            "A claim a reader can act on has to hold for the file it is written in."
        )
