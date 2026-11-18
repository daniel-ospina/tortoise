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

import re

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
_GJ = "tortoise/goal_judgement.py"

#: (document, cite token the document must still carry, target file, 1-based line, fragment at that line)
_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # --- docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md (search_engine.py +3, cache_projection.py +2)
    (_AUDIT, "cache_projection.py:3778", _CP, 3778, 'if table == "point"'),
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
    (_AUDIT, "cache_projection.py:2691-2702", _CP, 2691, "# \u26d4 PROVENANCE, PER ROW (#10965)"),
    (_AUDIT, "cache_projection.py:2703-2705", _CP, 2703, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    # Row 387's bare range, quoting "un-indexes a restored session". Its sibling pins (`2660-2662`,
    # `2650-2657`) are different ranges and do not reach this one. NON-VACUOUS: the pre-fix document
    # carried `:2648-2649` here, so the cite-token arm fires on that tree (measured).
    (_AUDIT, "`:2653-2654`", _CP, 2694, "un-indexes a restored session"),
    (_AUDIT, "cache_projection.py:2755", _CP, 2755, "tail_denied = forbidden | {"),
    # --- the rest of the header's enumerated `main` set, pinned by CONTENT like every other row.
    # `_MAIN_SET_MISSED` below pins the EIGHT the header was missing; these are the entries the header
    # already named. Without them the enumeration is a promise: `#13290` moved every `search_engine.py`
    # entry in it and NOTHING red — the document went on naming held addresses over a tree that had
    # moved them, and the reader rule sent the reader to the wrong line. A pin per entry is what turns
    # the list into a guard.
    (_AUDIT, "`:2877`", _SDK, 2877, "SET s.capture_redactions=$redactions"),
    (_AUDIT, "`sdk.py:37051`", _SDK, 37051, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    (_AUDIT, "`:37074`", _SDK, 37074, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "`audit.py:817`", _AU, 817, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2452`", _AU, 2452, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2463`", _AU, 2463, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`mcp_server.py:5089`", _MCP, 5089, "entity_type=entity_type"),
    (_AUDIT, "`:5529`", _MCP, 5529, "entity_type=entity_type"),
    (_AUDIT, "`:8034`", _MCP, 8034, "entity_type=entity_type"),
    (_AUDIT, "`:6923-6939`", _PI, 6923, "_FULLTEXT_INDEX_LABEL_FIELDS"),
    (_AUDIT, "`:6933`", _PI, 6933, '("Event", ["subject", "name"])'),
    (_AUDIT, "`:6934`", _PI, 6934, '("Subject", ["name"])'),
    # ⛔ The Object half of the SAME declaration had NO row, and that absence is ISSUE 1 of the
    # `#13489` review: §2.5's row cited `:6869`, which is now 54 lines short of the block (`:6923-6939`)
    # its three siblings (`:6933`, `:6934`, `:6939`) moved to — and 69 short of the Object declaration
    # (`:6938`) inside that block — `:6869` opens on `"pointKind",`,
    # an unrelated property list, so the cite read exactly like a correct one. An absent pin is not
    # coverage. ⚠️ That `:6869` was wrong AT THIS TREE and right at `main` (`ae54df166d`), which is
    # why the header's enumeration keeps `:6869` (its paragraph announces that head, not this one) and why
    # this row — the working-tree site — carries `:6938`.
    (_AUDIT, "`:6938`", _PI, 6938, '("Object", ["name"])'),
    (_AUDIT, "`:6939`", _PI, 6939, '("Source", ["_searchText"])'),
    (_AUDIT, "record_serve.py:468", _RS, 468, "READ_POINT_BY_ID: AnchoredRead("),
    (_AUDIT, "goal_judgement.py:1128", _GJ, 1128, "where = \"MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator\""),
    (_AUDIT, "sdk.py:24048", _SDK, 24048, "(n:Point {id:$id})-[:TAGGED]->()"),
    (_AUDIT, "search_engine.py:6721", _SE, 6721, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
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
    (_CENSUS, "cache_projection.py:2755", _CP, 2755, "tail_denied = forbidden | {"),
    # ⚠️ Row 13b's Source-writer cite, added because the site had NO pin at all — the same shape the
    # `:6938` row above was added for. It sat at `:19637` (a comment about provenance loss in the
    # `sessionCaptured` stamp block) for merges without a red, because `rg -n '19637' docs/ tests/
    # tools/ config/` reached this one row and no `_PINS` / `_TOKEN_OCCURRENCES` entry covered it.
    (_AUDIT, 'sdk.py:21692', _SDK, 21692, 'search_text = _source_search_text(topics=topics)'),
    (_AUDIT, 'sdk.py:21734', 'tortoise/sdk.py', 21734, 's.summary=CASE WHEN $sum'),
    (_AUDIT, '`:21857`', 'tortoise/sdk.py', 21857, 's.summary, s.topics, s.'),
    (_AUDIT, 'hosted_api.py:22925', 'tortoise/hosted_api.py', 22925, 'MERGE (s:Source {url:$did})'),
    (_AUDIT, '`:22737`', 'tortoise/hosted_api.py', 22737, 'def _execute_commit_writes'),
    (_AUDIT, '`:33601`', 'tortoise/hosted_api.py', 33601, 'MERGE (p:PackInstall {namespace: $ns})'),
    # Row 13's family list carries two BARE tokens with no row of their own, so no count
    # could be declared for them and re-pointing a single site left a sibling to satisfy
    # the substring arm — measured: mutating the list's `:2715` to `:2716` left 22 passed.
    (_AUDIT, "`:2715`", _CP, 2755, "tail_denied = forbidden | {"),
    (_AUDIT, "`:2755`", _CP, 2755, "tail_denied = forbidden | {"),
)

#: The `main`-set group the `#13124` review found OUTSIDE the header's `main` enumeration: (token the
#: header must carry, target file, 1-based line at `main`, verbatim fragment on that line). Five of these
#: are reached by `_PINS` already; `:2433`, `:2490` and `projection/entities.py:5646` were in NO pin at all
#: before this one, and the header named none of the eight — which is the defect
#: `test_the_main_set_enumeration_names_the_group_it_was_missing` exists for.
_ENT = "tortoise/projection/entities.py"
_MAIN_SET_MISSED: tuple[tuple[str, str, int, str], ...] = (
    (":2433", _CP, 2464, "def _episodic_source_search_text_is_the_lexicon"),
    (":2490", _CP, 2521, 'return summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"'),
    (":2653-2660", _CP, 2693, "it is the ONLY field the Source FTS index"),
    (":2653-2654", _CP, 2694, "un-indexes a restored session"),
    (":2663-2665", _CP, 2703, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    (":2715", _CP, 2755, "tail_denied = forbidden | {"),
    # These two bare tokens live in row 13's family list. Without a pin row of their
    # own, no count can be declared for them, and re-pointing a SINGLE site leaves a
    # sibling to satisfy the substring arm — measured: mutating the list's `:2715` to
    # `:2716` left 22 passed. Each row makes its site load-bearing.
    (":3735", _CP, 3778, 'if table == "point"'),
    (":5646", _ENT, 5666, "s.summary=coalesce($summary, s.summary, '')"),
    # Round 8: written at `main`, pinned at `main`, but absent from the enumeration — so the header's rule
    # sent readers to the pin. ⛔ NOT "where every one is a comment": at the pin `sdk.py:23982` is a DELETE
    # statement and `hosted_api.py:22919` is `if is_subject_kind(...)`, both executable. The pin is wrong for
    # all six; it is a comment for most, and unrelated CODE for the two that matter most here.
    ("sdk.py:24048", _SDK, 24048, "(n:Point {id:$id})-[:TAGGED]->()"),
    ("search_engine.py:6721", _SE, 6721, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    ("sdk.py:21734", _SDK, 21734, "s.summary=CASE WHEN $sum"),
    (":21857", _SDK, 21857, "s.summary, s.topics, s."),
    ("hosted_api.py:22925", "tortoise/hosted_api.py", 22925, "MERGE (s:Source {url:$did})"),
    (":22737", "tortoise/hosted_api.py", 22737, "def _execute_commit_writes"),
)


#: How many times each pinned token must appear in its document. `_resolve`'s token arm is an EXISTENCE
#: test over the whole file, so a token occurring twice survives a re-point at ONE site: the sibling
#: keeps the arm green, and the content arm still passes because it opens the TARGET file, which such an
#: edit does not touch. Measured on the row this test was written for — `cache_projection.py:3778` occurs
#: 3x, and re-pointing one occurrence to `:3738` red 0 of 7 tests. Counts make every SITE load-bearing
#: instead of every STRING; the `#13083` review that found it named 15 shared tokens, which is why this
#: is a set of counts and not another context-bearing token.
_TOKEN_OCCURRENCES: dict[tuple[str, str], int] = {
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:3778'): 3,
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
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2715`'): 4,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2755`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2691-2702'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2703-2705'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2653-2654`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2755'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2877`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:37051`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:37074`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`audit.py:817`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2452`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2463`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`mcp_server.py:5089`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5529`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:8034`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6923-6939`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6933`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6934`'): 1,
    # 1, not 2: `:6938` is the Object declaration at THIS tree, and only §2.5's row cites it. The
    # site the header's enumeration names is `:6869` — that paragraph is bound to `main` (`ae54df166d`),
    # where the declaration IS `:6869` (`:6938` there is `# background RDB/AOF child in the fork
    # slot…`). `#13489`'s first cut re-pointed the header too, which made a history cite claim a
    # working-tree address: the FALSE CORRECTION this file exists to stop, one level up.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6938`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6939`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'record_serve.py:468'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'goal_judgement.py:1128'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:24048'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:6721'): 2,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', 'tortoise/search_engine.py:3449'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:3447`'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:2886'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'search_engine.py:2728'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:4904'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', '`search_engine.py:4823`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'search_engine.py:3079-3094'): 1,
    ('docs/evidence/13083-capability-census/README.md', 'cache_projection.py:2755'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21692'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21734'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:21857`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:22925'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:22737`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:33601`'): 1,
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
    the counts still read `322`/`216`/`106` for a file that measures `326`/`214`/`112`, and the pin-side
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
        "122 of this document's 214",
        "dedupes the 326 as-written spellings to 214 — 112",
        "326 written spellings → 214 pairs → 112 collisions → 122 drifters of the 214 comparable",
        # ⛔ The RULE, not the bare sha: the sha occurs 8x in the file, so asserting the string alone let a
        # revert of the reader-rule sentence (back to `340d500491`) pass green.
        "an address in it opens at `main` `ae54df166d`",
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
        # A SELF-INFLICTED one: an alternative counting basis was described as "±5 … the numerator does not
        # move — it is 124 under either basis". Both halves are claims about MEASUREMENT, and both were
        # asserted rather than measured: the basis-INVARIANCE is false (counting the two-code-span pair adds
        # `cache_projection.py:2433`, a `main`-set address, so the numerator moves to 124 from 123), and "±5"
        # names a fixed offset for a denominator that moves by whatever the counting decision moves it by. A
        # reader told to compare numerators across bases would have "corrected" a correct figure by
        # arithmetic — the offset-not-content failure, one level down.
        # ⛔ The absence guard forbids the CLAIMS, not the numbers. `"216 pairs"` was in an earlier form of
        # this tuple and it reds on CORRECT text the moment a later pass measures 216 pairs and says so —
        # an over-block that reads as a finding. Guard the sentence a regression would restore, never a bare
        # figure that a re-measurement may legitimately produce.
        "does not move",
        "moves by ±5",
        "under either basis",
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
    # 48: `#13489`'s review added the `:6938` row (`("Object", ["name"])`, the §2.5 address that
    # had NO pin at all, which is how `:6869` stayed behind its three siblings — `:6933`/`:6934`/`:6939`
    # sit 64/65/70 lines below `:6869`, while `:6938`, the row that had no pin, is the 69th), and row
    # 13b's Source-writer pin (`sdk.py:21692`) is the same shape added again, for the same reason.
    assert len(_PINS) == 50


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
    # ⛔ Scoped to the CLAUSE, not the whole document: these are bare addresses, and any legitimate
    # `:6549` spelled elsewhere in the census would red this arm on correct text.
    for shifted in ("`:3084`, `:4674-4678`, `:6549`", "walk at `:4674`", "operator leg at `:6549`"):
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

    NON-VACUOUS, and WITNESSED AT A COMMIT, not a moving ref: the pre-fix document carries the second
    spelling (`git show 8ba6de4438:docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md |
    grep -c '`none`: `search_engine.py:4350-4351`'` -> 1; `43028d178b` also -> 1). ⚠️ `origin/main` reads
    **0** here — it has since been re-pointed — so a ref would have made this witness vacuous while still
    looking like evidence.

    ⛔ **AND THE DEFECT RETURNED ON `main` UNDER A DIFFERENT NUMBER — which is why the third assertion below
    exists.** `#13290` re-pointed this paragraph by a delta and landed on `:4489-4490`: also a plain label
    scan, but a DIFFERENT one — `run_vector_query`'s `MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN
    count(*)`, not the `run_structural_query` scan the table row quotes at `:4708-4710`. Measured on
    `origin/main` at this re-cut: the table row names `:4708-4710`, the verdict paragraph names
    `:4489-4490` — two addresses for one reader, in one table, again. The `:4350-4351` assertion cannot see
    it (`origin/main` reads 0 there), so this test would have passed over the live defect; the
    `:4489-4490` assertion is what makes it reachable.
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
    assert "`none`: `search_engine.py:4489-4490`" not in text, (
        "§2.1's verdict paragraph names `:4489-4490`, which is `run_vector_query`'s "
        "`MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(*)` — a different plain label "
        "scan from the `run_structural_query` one the table row quotes at `:4708-4710`. `#13290` reached "
        "it by applying a delta to `:4350-4351` instead of opening the statement, which is the same "
        "offset-not-content failure as the `:4549` re-point this file was written for. Re-derive from "
        "the fragment (`grep -n 'MATCH (n:{label_str})'`), never from the address beside it."
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

    * CONTENT — each of the eight carries, **on this branch's tree** (the tree this test reads),
      the fragment the document quotes for it. `_PINS` reaches five of them; `:2433`, `:2490` and
      `projection/entities.py:5646` were in NO pin before this one. ⛔ The earlier form of this
      bullet justified the same rows as "at `main` (the working tree: this branch is docs-only, so
      `main`'s `.py` files are this tree's)" — that was true when it was written and is FALSE for
      the rows `#13363` re-derived: `sdk.py` moved `+66` and `hosted_api.py` `+6`/`+18`, so those
      rows carry THIS tree's figures, not `main`'s. A row here is a claim about `REPO / path`, never
      about `main`'s copy of it — the same thing `_MAIN_SET_MISSED`'s own comment says.
    * CLAIM — the header still NAMES them, and no longer states the three absolutes the review measured
      false, nor the "pin-written remainder" label whose denominator contradicted it.

    NON-VACUOUS on the pre-fix document (`git show 295c26ebc8:<the file>`): the header region — every
    line before `## 1.` — carries **0** occurrences of `cache_projection` and **1** of each of the three
    absolutes, so every assertion below reds there. The region bound is what makes the token arm
    discriminating: `:2433`, `:2490` and `:2715` DO appear later in the document (row 13, §2.5), so an
    unbounded `in text` check would pass on the broken file.
    """
    # ⛔ THE POPULATION HAS AN ANTI-SHRINK ARM (#13509), the mirror of the one the sibling
    # `_MAIN_HEAD_FIGURES` guard carries. BOTH use sites below ITERATE this tuple, so a row deleted to
    # silence a re-point red simply stops existing in both: its content pin and the "the header names
    # it" assertion vanish together and nothing reds — the false PASS the sibling arm was added for,
    # one table over. Fourteen is the `main`-set group the `#13124` review measured; a fifteenth is a
    # re-derivation to make deliberately, not silently.
    assert len(_MAIN_SET_MISSED) == 14, (
        f"_MAIN_SET_MISSED carries {len(_MAIN_SET_MISSED)} rows, not 14 — both use sites iterate this "
        "tuple, so a row deleted to make a re-point pass is the coverage loss neither arm below can "
        "see. Restore the row (the addresses are enumerated in the audit header's `main` set) or "
        "update this count with the re-derivation that justifies it")
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
        (
            "every other `file:line` here was written at the pin and opens at `ccb49741b2ec`",
            "it is the third absolute the review measured false: a `main`-set address opens at `main`, "
            "not at the pin, and the sentence contradicted the enumeration two lines above it",
        ),
    ):
        assert stale not in text, (
            f"the header still claims {stale!r}, which the `#13124` review measured false ({why}). "
            "A claim a reader can act on has to hold for the file it is written in."
        )


def test_the_headers_enumeration_is_never_re_pointed_to_the_working_tree() -> None:
    """The header enumeration's numbers are bound to the `main` head the paragraph announces — `:6869` is
    that head's cite.

    ⚠️ This region is the HEADER, not §1: the binding sentence sits in the preamble, ABOVE `## 1.`, which
    opens a hundred-odd lines below it. The file's own name for the region is "the header region — every line
    before `## 1.`" (see `test_the_main_set_enumeration_names_the_group_it_was_missing`), and §1 is 18 rows of
    writers, which carries no enumeration of cites at all. ⛔ No line numbers are asserted here on purpose:
    the first cut of this docstring said "line 113" and "`## 1.` at line 209", and the very text it was
    describing moved both — a positional claim about the document, inside the document's own guard.

    The header paragraph opens with its own binding: *"All sixteen were re-derived at `main` (`ae54df166d`)
    — ⚠️ **so they do NOT open at the pin above** — and each is named here by the cite it became"*. Every
    number in that sentence is therefore an address AT `ae54df166d`, not at this tree.

    `#13489`'s first cut re-pointed the Object entry from `:6869` to `:6938` — this tree's value, correct
    for §2.5's row — the Object declaration there is `:6938`, which is `:6869` + 69 IN
    `projection/__init__.py` (an address delta, not a document distance) — and WRONG under a sentence
    that announces the other head. A
    `⚠️ CORRECTED` marker records that the text changed, never that the change was right: the
    FALSE CORRECTION this file's own docstring is built around, one head over.

    NON-VACUOUS on the pre-fix document (`git show 26a68370b327:docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md`):
    the header carries `:6938` after the fragment and `:6869` nowhere, so this assertion reds there. The
    measurement that settles which number belongs to the announced head is
    `git show ae54df166d:tortoise/projection/__init__.py | sed -n '6869p;6938p'` ->
    `("Object", ["name"]),` and then `# background RDB/AOF child in the fork slot, and a hung un-reaped`.

    ⚠️ The assertion is on the `:6869` ADJACENCY, not on `:6938`'s absence — but it does NOT follow
    that disclosure is free, and an earlier form of this docstring said it was. The declaration must sit
    DIRECTLY above `` :6869`; ``; anything between them breaks the adjacency this arm reads. And
    disclosing this tree's figure appends a SECOND `:6938` occurrence, which
    `test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence` counts at 1. So a header that
    names both heads is a TWO-file edit, not a rewrite of this one: keep the declaration above
    `` :6869`; ``, put the disclosure AFTER the semicolon, and move `_TOKEN_OCCURRENCES[':6938']` to 2
    in the same change. ⛔ Both arms are correct — do not clear a red by loosening either one; that
    deletes the only protection on the entry that actually suffered the false correction. The document's
    own shape puts the branch figure FIRST with the head in parens (`:33601` **on this branch**
    (`:33583` at the head named above)), not the reverse.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    assert '`("Object", ["name"])`\n`:6869`;' in text, (
        "the header enumeration no longer names the Object declaration at `:6869` — the address it holds "
        "at the `main` head that paragraph announces (`ae54df166d`). `:6938` is THIS tree's value (and "
        "§2.5's row); putting it here re-points a history cite to a working-tree address, the "
        "`⚠️ CORRECTED` failure this file exists to stop. Restore `:6869` directly above a `;`. To ALSO "
        "disclose this tree's figure, keep the declaration where it is, append the disclosure AFTER the "
        "semicolon, and bump `_TOKEN_OCCURRENCES[':6938']` to 2 in the same change — otherwise "
        "`test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence` reds. Do not loosen either arm."
    )


#: The `main`-head figures the header enumerates beside this tree's — one number per moved site, each
#: re-opened at `ae54df166d`. They are NOT in `_PINS`: a pin resolves against the WORKING TREE, and a
#: `main`-head address has no working-tree line to resolve to, so `declared == pinned` would force them
#: out of `_TOKEN_OCCURRENCES` too — which is exactly what happened when this revision re-pointed the
#: document and took the counts with the old spellings. Measured on `origin/main`: the same figures DID
#: carry counts (`':6854-6870'`: 1, `'sdk.py:36985'`: 1), so losing them was a REGRESSION in coverage,
#: not a pre-existing gap. Counting the spelling here restores it without touching the pin contract.
#:
#: ⛔ This is a count guard, not a head guard: it catches a re-point that changes how many sites carry
#: the figure (the `:4549`→`:4552` double-shift, and every `⚠️ CORRECTED` edit that moves one), and
#: nothing more. Proving each figure OPENS on its content at `ae54df166d` needs a head-selecting arm —
#: `git show <head>:<path> | sed -n '<N>p'` — which is #13508, deliberately not solved by a test that
#: would need full history in a shallow CI checkout.
_MAIN_HEAD_FIGURES: dict[str, int] = {
    '`:6854-6870`': 1,
    '`:6864`': 1,
    '`:6865`': 1,
    '`:6870`': 1,
    '`:36985`': 2,
    '`:37008`': 2,
    '`:23982`': 1,
    '`:21668`': 1,
    '`:21791`': 1,
    '`:22919`': 2,
    '`:22731`': 2,
    '`:33583`': 1,
}


def test_the_main_head_figures_the_header_names_are_still_counted() -> None:
    """A `main`-head figure cannot be pinned to the tree, so count it — that is what caught it before.

    Every one of these twelve is an address AT `ae54df166d`, written beside this tree's figure for the
    same site. Re-pointing one while the `main` label stays is this document's core defect (a
    `⚠️ CORRECTED` marker records that the text changed, never that the change was right), and it moves
    a count: the token stops appearing where it was, or starts appearing where it was not.

    ⛔ What this does NOT prove: that the figure is the RIGHT line at `ae54df166d`. It proves the figure
    written is the figure this file last measured. A wrong-but-stable number passes; #13508 carries the
    head-selecting arm that would actually open it.
    """
    # ⛔ THE POPULATION HAS AN ANTI-SHRINK ARM, and it is the file's own precedent applied to a new
    # dict (`assert len(_PINS) == 50` above, `declared == pinned` in the sibling pin test). Without
    # it the guard above is defeatable exactly the way an absent pin is not coverage: re-point a
    # `main`-head figure in the document AND delete its row here, and the loop has nothing to check
    # — measured on a copy of this tree at `25b0fd5f` (`:6854-6870` -> `:6855-6871` in the audit doc
    # plus the row deleted) -> `1 passed, 10 deselected`, GREEN over a false correction. With the
    # row present the same re-point reds. The count is the twelve addresses the header names beside
    # this tree's figures, and a thirteenth is a re-derivation to make deliberately, not silently.
    assert len(_MAIN_HEAD_FIGURES) == 12, (
        f"_MAIN_HEAD_FIGURES carries {len(_MAIN_HEAD_FIGURES)} rows, not 12 — this dict is the only "
        "reader of the `main`-head figures the audit header names, so a row deleted to make the "
        "count guard pass is the coverage loss the loop above cannot see. Restore the row (the "
        "figures are listed beside this tree's in the header) or update this count with the "
        "re-derivation that justifies it")
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    for token, expected in _MAIN_HEAD_FIGURES.items():
        actual = text.count(token)
        assert actual == expected, (
            f"{_AUDIT} carries `{token}` {actual}x, not {expected}x. These are the `main`-head figures "
            "(`ae54df166d`); a changed count means one was re-pointed, which is the false correction "
            "this document is built around. Re-open the site by CONTENT — "
            "`git show ae54df166d:<path> | sed -n '<N>p'` — and fix the figure, not the count."
        )


#: The header states how far each site MOVED between the `main` head it names and this
#: tree. A wrong delta is worse than a wrong address: a reader applies it as an OFFSET,
#: which is the drift the header exists to stop. Nothing asserted these literals —
#: measured: `**+66**` -> `**+65**` at one site left this file at **11 passed**.
_DECLARED_DELTAS: tuple[tuple[str, int], ...] = (
    ("`sdk.py` moved **+66**", 1),
    ("`+66`/`+69`/`+6`", 1),
    ("`projection/__init__.py` moved **+69**", 1),
    ("**+280**", 1),
    ("`hosted_api.py` moved **+6**", 1),
    ("**+18**", 1),
)


def test_the_declared_move_deltas_are_the_ones_the_header_states() -> None:
    """A moved figure must red here, not be discovered by a reader applying it."""
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    for token, expected in _DECLARED_DELTAS:
        actual = text.count(token)
        assert actual == expected, (
            f"{_AUDIT} carries {token!r} {actual}x, not {expected}x — the header's own delta "
            "figure changed. Re-derive it from the two heads the sentence names "
            "(`git show ae54df166d:<path> | sed -n '<N>p'` versus `sed -n '<M>p' <path>`) and "
            "update the claim; do not nudge it."
        )
