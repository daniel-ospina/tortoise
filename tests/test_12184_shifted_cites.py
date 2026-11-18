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
_INDEX = "docs/00_index.md"
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
    (_AUDIT, "cache_projection.py:3841", _CP, 3841, 'if table == "point"'),
    (_AUDIT, "search_engine.py:4797-4801", _SE, 4801, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)"),
    (_AUDIT, "search_engine.py:6684", _SE, 6684, "(op)-[r2:IMPL|hasPart]-(other:Point)"),
    (_AUDIT, "search_engine.py:7043", _SE, 7043, "(n:Point)-[r:aboutSubject]->(:Subject)"),
    (_AUDIT, "search_engine.py:7045", _SE, 7045, "(m:Event)-[r2:aboutSubject]->(:Subject)"),
    # Row 149's TWO BARE range cites carry no `search_engine.py` prefix, so the token-level pins for
    # `6659`/`6661` above do not reach them — they are the only shifted mentions in this document the
    # first pin set missed. Both were already correct at the pre-fix head, so these two are
    # COMPLETENESS pins: they catch the re-point/double-shift that DID happen in this class (`:4549`
    # re-pointed to `:4552`), which is the failure the whole file exists for.
    (_AUDIT, "`:7042-7045`", _SE, 7042, "_SUBJECT_SOURCE_SCOPED_PROBE = ("),
    (_AUDIT, "`:7048-7061`", _SE, 7048, "def subject_binding_available"),
    (_AUDIT, "search_engine.py:3100", _SE, 3100, "CALL db.idx.fulltext.queryNodes"),
    (_AUDIT, "| `tortoise/search_engine.py:4725-4727` |", _SE, 4725, "MATCH (n:{label_str})"),
    # ⚠️ The token above carries its TABLE context on purpose. A bare `search_engine.py:4708-4710` is a
    # SUBSTRING of §2.1's verdict token (`` `none`: `search_engine.py:4708-4710` ``), so the verdict row
    # satisfies this one and the TABLE row can be re-pointed alone without reddening anything — measured:
    # mutate the table to `:4511-4513` with the bare token -> 0 red, with the table-context token -> 1 red.
    (_AUDIT, "search_engine.py:4140", _SE, 4140, "CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit"),
    (_AUDIT, "search_engine.py:3005", _SE, 3005, 'label = "Source" if entity_type == "document"'),
    (_AUDIT, "`:3072`", _SE, 3072, "the fulltext index is on Source._searchText"),
    (_AUDIT, "cache_projection.py:2712-2723", _CP, 2712, "# \u26d4 PROVENANCE, PER ROW (#10965)"),
    (_AUDIT, "cache_projection.py:2724-2726", _CP, 2724, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    # Row 387's bare range, quoting "un-indexes a restored session". Its sibling pins (`2660-2662`,
    # `2650-2657`) are different ranges and do not reach this one. NON-VACUOUS: the pre-fix document
    # carried `:2648-2649` here, so the cite-token arm fires on that tree (measured).
    (_AUDIT, "`:2714-2715`", _CP, 2715, "un-indexes a restored session"),
    (_AUDIT, "cache_projection.py:2776", _CP, 2776, "tail_denied = forbidden | {"),
    # --- the rest of the header's enumerated `main` set, pinned by CONTENT like every other row.
    # `_MAIN_SET_MISSED` below pins the EIGHT the header was missing; these are the entries the header
    # already named. Without them the enumeration is a promise: `#13290` moved every `search_engine.py`
    # entry in it and NOTHING red — the document went on naming held addresses over a tree that had
    # moved them, and the reader rule sent the reader to the wrong line. A pin per entry is what turns
    # the list into a guard.
    (_AUDIT, "`:2882`", _SDK, 2882, "SET s.capture_redactions=$redactions"),
    (_AUDIT, "`sdk.py:37067`", _SDK, 37067, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    (_AUDIT, "`:37090`", _SDK, 37090, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "`audit.py:817`", _AU, 817, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2452`", _AU, 2452, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2463`", _AU, 2463, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`mcp_server.py:5089`", _MCP, 5089, "entity_type=entity_type"),
    (_AUDIT, "`:5529`", _MCP, 5529, "entity_type=entity_type"),
    (_AUDIT, "`:8034`", _MCP, 8034, "entity_type=entity_type"),
    (_AUDIT, "`:6923-6939`", _PI, 6923, "_FULLTEXT_INDEX_LABEL_FIELDS"),
    (_AUDIT, "`:6933`", _PI, 6933, '("Event", ["subject", "name"])'),
    (_AUDIT, "`:6934`", _PI, 6934, '("Subject", ["name"])'),
    (_AUDIT, "`:6939`", _PI, 6939, '("Source", ["_searchText"])'),
    # The FIFTH declaration in the same block, and the one the re-derivation missed: §2.5 row 8's
    # `("Object", ["name"])` cite kept the pre-merge `:6869` while its four siblings in the same
    # sentence were re-pointed, and `:6869` is `"pointKind",` inside `_POINT_RANGE_INDEX_PROPS` at
    # this head (it IS `("Object", ["name"])` at `main` `ae54df166d`, which is why the enumeration
    # in §1 keeps `:6869` as correct HISTORY — see the `hosted_api.py:33583` precedent). Unpinned,
    # the row sent a reader to the wrong line in the document that tells them to distrust numbers.
    (_AUDIT, "`:6938`", _PI, 6938, '("Object", ["name"])'),
    (_AUDIT, "record_serve.py:468", _RS, 468, "READ_POINT_BY_ID: AnchoredRead("),
    (_AUDIT, "goal_judgement.py:1128", _GJ, 1128, "where = \"MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator\""),
    (_AUDIT, "sdk.py:24064", _SDK, 24064, "(n:Point {id:$id})-[:TAGGED]->()"),
    (_AUDIT, "search_engine.py:6738", _SE, 6738, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    # --- docs/architecture/SUBSTRATE-REBUILD-MAP.md §5's move list (the #11682 corpus-join block)
    (_SUBSTRATE, "tortoise/search_engine.py:3451", _SE, 3451, "``:Source``/``:Document`` row must not contribute"),
    (_SUBSTRATE, "`:3449`", _SE, 3449, "# #11682: the corpus join is PER FAMILY"),
    # --- docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md
    (_M3_BRIEF, "tortoise/search_engine.py:2888", _SE, 2888, "def run_fts_query"),
    (_M3_BRIEF, "search_engine.py:2730", _SE, 2730, "def recency_tie_clause"),
    (_M3_BRIEF, "tortoise/search_engine.py:4921", _SE, 4921, "for rank, (pid, _score) in enumerate(ranked)"),
    (_M3_BRIEF, "`search_engine.py:4840`", _SE, 4840, "def rrf_fusion("),
    # --- docs/architecture/ENTITY-VERDICT-BANDS.md — the D-1 row "the retrieval filter is status-only"
    # ⚠️ This row is a SECOND false correction, of the same class this file exists for. The document
    # cited `search_engine.py:2973-2997`; other PRs' inserts had already moved the status filter to
    # `:3020` on `origin/main`, so the cite was ~44 lines stale BEFORE this branch's `+3`. Adding the
    # `+3` (`2973-2997` -> `2976-3000`) therefore preserved the staleness while reading as a correction,
    # and `2976-3000` is the `build_or_query` byte-identity comment block, not the filter. The pin below
    # is anchored on the line that IS the filter, so the next offset-only edit reds instead of shipping.
    (_EVIDENCE, "search_engine.py:3081-3096", _SE, 3081, "if label in _STATUS_FILTERED_LABELS"),
    # --- the D-1 dedup filter, in BOTH documents that promise to carry the SAME address.
    # ⛔ `#13345`'s re-derivation moved these to `27866` / left them at `27861` by adding a line delta,
    # and the pair then named two different lines, neither of which was the filter (measured at
    # `d1bfef778f` and at every later head: `27866` is prose and `27861` is the PRE-`#13290` value).
    # The row above is what makes "carries the same number" a fact instead of a promise; these two are
    # what make the NUMBER the live site rather than merely a matching one.
    (_EVIDENCE, "sdk.py:28019", _SDK, 28019, "n.dedup_reviewed IS NULL"),
    (_INDEX, "sdk.py:28019", _SDK, 28019, "n.dedup_reviewed IS NULL"),
    # --- docs/evidence/13083-capability-census/README.md
    (_CENSUS, "cache_projection.py:2776", _CP, 2776, "tail_denied = forbidden | {"),
    (_AUDIT, 'sdk.py:21750', 'tortoise/sdk.py', 21750, 's.summary=CASE WHEN $sum'),
    (_AUDIT, '`:21873`', 'tortoise/sdk.py', 21873, 's.summary, s.topics, s.'),
    # ⚠️ Row 13b's Source-writer cite, added by the peer lane because the site had NO pin at
    # all. Its number is re-derived by CONTENT at THIS tree, not carried from theirs —
    #   grep -n 'search_text = _source_search_text(topics=topics)' tortoise/sdk.py
    # -> 21708, the capture writer's actual `_searchText` assignment (the `#3518`
    # contract), not the comment above it and not the `sessionCaptured` provenance block
    # an earlier pin sat on.
    (_AUDIT, 'sdk.py:21708', _SDK, 21708, 'search_text = _source_search_text(topics=topics)'),
    (_AUDIT, 'hosted_api.py:22925', 'tortoise/hosted_api.py', 22925, 'MERGE (s:Source {url:$did})'),
    (_AUDIT, '`:22737`', 'tortoise/hosted_api.py', 22737, 'def _execute_commit_writes'),
    # ⛔ The document carries TWO addresses for this one statement on purpose: `:33583` is the number
    # at the `main` head the paragraph names (`ae54df166d`, verified: that line IS the MERGE there),
    # and `:33601` is the live one after this branch's own insert above it. The pin follows the LIVE
    # one, because a pin on the historical number would be reshaped by every future insert above it.
    (_AUDIT, '`:33601`', 'tortoise/hosted_api.py', 33601, 'MERGE (p:PackInstall {namespace: $ns})'),
)

#: The `main`-set group the `#13124` review found OUTSIDE the header's `main` enumeration: (token the
#: header must carry, target file, 1-based line in the WORKING TREE (`_resolve` reads `REPO`, so a branch
#: that moves `.py` files shifts these with it), verbatim fragment on that line). Five of these
#: are reached by `_PINS` already; `:2485`, `:2542` and `projection/entities.py:5666` were in NO pin at all
#: before this one, and the header named none of the eight — which is the defect
#: `test_the_main_set_enumeration_names_the_group_it_was_missing` exists for.
_ENT = "tortoise/projection/entities.py"
_MAIN_SET_MISSED: tuple[tuple[str, str, int, str], ...] = (
    (":2485", _CP, 2485, "def _episodic_source_search_text_is_the_lexicon"),
    (":2542", _CP, 2542, 'return summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"'),
    (":2714-2721", _CP, 2714, "it is the ONLY field the Source FTS index"),
    (":2714-2715", _CP, 2715, "un-indexes a restored session"),
    (":2703-2705", _CP, 2724, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    (":2755", _CP, 2776, "tail_denied = forbidden | {"),
    (":3778", _CP, 3841, 'if table == "point"'),
    (":5666", _ENT, 5666, "s.summary=coalesce($summary, s.summary, '')"),
    # Round 8: written at `main`, pinned at `main`, but absent from the enumeration — so the header's rule
    # sent readers to the pin. ⛔ The pin is wrong for all six, and BOTH readings of HOW were
    # measured at `ccb49741b2ec`, so they are reconciled here rather than one displacing the
    # other: the pin DOES hold a DELETE at `sdk.py:23982` (`... DELETE r"`), which is why
    # "every one is a comment" is false — but `sdk.py:24010` is `# creates a PARALLEL edge on
    # attribute change; the two-` and there is NO `DELETE` in 23990-24040, so a DELETE elsewhere
    # in the file is not a DELETE in this region. `hosted_api.py:22919` IS
    # `if is_subject_kind(_ekind or ""):` and `:22925` is the `)` closing `create_entity(...)`.
    ("sdk.py:24064", _SDK, 24064, "(n:Point {id:$id})-[:TAGGED]->()"),
    ("search_engine.py:6738", _SE, 6738, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    ("sdk.py:21750", _SDK, 21750, "s.summary=CASE WHEN $sum"),
    (":21873", _SDK, 21873, "s.summary, s.topics, s."),
    ("hosted_api.py:22925", "tortoise/hosted_api.py", 22925, "MERGE (s:Source {url:$did})"),
    (":22737", "tortoise/hosted_api.py", 22737, "def _execute_commit_writes"),
)


#: How many times each pinned token must appear in its document. `_resolve`'s token arm is an EXISTENCE
#: test over the whole file, so a token occurring twice survives a re-point at ONE site: the sibling
#: keeps the arm green, and the content arm still passes because it opens the TARGET file, which such an
#: edit does not touch. Measured on the row this test was written for — `cache_projection.py:3841` occurs
#: 3x, and re-pointing one occurrence to `:3738` red 0 of 7 tests. Counts make every SITE load-bearing
#: instead of every STRING; the `#13083` review that found it named 15 shared tokens, which is why this
#: is a set of counts and not another context-bearing token.
_TOKEN_OCCURRENCES: dict[tuple[str, str], int] = {
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:3841'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4797-4801'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:6684'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7043'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7045'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7042-7045`'): 4,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7048-7061`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3100'): 5,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '| `tortoise/search_engine.py:4725-4727` |'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4140'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3005'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3072`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2712-2723'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2724-2726'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'sdk.py:28019'): 1,
    ('docs/00_index.md', 'sdk.py:28019'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2714-2715`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2776'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2882`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:37067`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:37090`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`audit.py:817`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2452`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2463`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`mcp_server.py:5089`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5529`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:8034`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6923-6939`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6933`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6934`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6939`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6938`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'record_serve.py:468'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'goal_judgement.py:1128'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:24064'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:6738'): 2,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', 'tortoise/search_engine.py:3451'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:3449`'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:2888'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'search_engine.py:2730'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:4921'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', '`search_engine.py:4840`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'search_engine.py:3081-3096'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6938`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'sdk.py:28019'): 1,
    ('docs/00_index.md', 'sdk.py:28019'): 1,
    ('docs/evidence/13083-capability-census/README.md', 'cache_projection.py:2776'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21750'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:21873`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21708'): 1,
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


def test_the_dedup_filter_is_one_address_in_both_documents_that_promise_it() -> None:
    """The CROSS-DOCUMENT arm — `_PINS` cannot see it.

    `docs/00_index.md`'s D-1 row states the promise in as many words: *"the filter it names is at
    `sdk.py:<n>` at this head, and `ENTITY-VERDICT-BANDS.md` carries the same number"*. Every row in
    `_PINS` is satisfied by its OWN document, so two documents naming two DIFFERENT lines both pass every
    pin — which is the shipped `#13345` state, measured: `docs/00_index.md` `sdk.py:27866` (prose at the
    head, and the filter at no head) against `ENTITY-VERDICT-BANDS.md` `sdk.py:27861` (the value before
    `#13290` moved it). The row that claims agreement was the only thing that had to hold, and nothing
    checked it: the `+5` delta was applied to one document and not the other, and the promise broke
    while reading as a correction.

    NON-VACUOUS on that tree: the two addresses differ (`27866` vs `27861`), so the first assertion reds;
    and neither opens on `dedup_reviewed IS NULL`, so the second reds independently. The address is
    read out of each document by regex rather than hardcoded, so this arm survives a legitimate future
    re-derivation of BOTH rows together (which is the only edit that keeps the promise true).
    """
    def address_beside(doc_rel: str) -> str:
        lines = (REPO / doc_rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "dedup_reviewed" not in line:
                continue
            # `ENTITY-VERDICT-BANDS.md` puts the address on the line AFTER the marker; `00_index.md`
            # carries both on one line. One line of slack covers both, and the regex is anchored on
            # the qualified `sdk.py:` form so a bare `:NNNN` elsewhere on the line cannot be picked up.
            window = " ".join(lines[i:i + 2])
            found = re.search(r"sdk\.py:(\d{4,5})", window)
            if found:
                return found.group(1)
        raise AssertionError(
            f"{doc_rel} carries no `sdk.py:NNNN` address beside its `dedup_reviewed` marker — the D-1 "
            "filter cite was dropped rather than re-derived."
        )

    index_addr = address_beside(_INDEX)
    bands_addr = address_beside(_EVIDENCE)
    assert index_addr == bands_addr, (
        f"the two documents disagree on the D-1 dedup filter: `docs/00_index.md` says `sdk.py:{index_addr}` "
        f"and `ENTITY-VERDICT-BANDS.md` says `sdk.py:{bands_addr}`, while `docs/00_index.md` promises the "
        "latter \"carries the same number\". Only one address can open at any head, so one of the two "
        "readers is sent to the wrong line. Re-derive BOTH from the statement "
        "(`grep -n 'dedup_reviewed IS NULL' tortoise/sdk.py`) rather than re-pointing one by a delta."
    )
    target = (REPO / _SDK).read_text(encoding="utf-8").splitlines()
    assert 0 < int(bands_addr) <= len(target), (
        f"`sdk.py:{bands_addr}` is out of range ({len(target)} lines) — the file shrank under the cite."
    )
    assert "dedup_reviewed IS NULL" in target[int(bands_addr) - 1], (
        f"both documents agree on `sdk.py:{bands_addr}`, but that line is "
        f"{target[int(bands_addr) - 1].strip()[:100]!r} — agreeing on the wrong line is still wrong, and "
        "it is the reason this arm checks content as well as agreement."
    )


def test_the_measured_figures_and_the_pin_side_descriptions_are_the_re_measured_ones() -> None:
    """REVERT GUARD, not a re-measurement — say what this does NOT prove.

    The drift figures and the two pin-side descriptions below were each corrected by the `#13083` review:
    the counts still read `322`/`216`/`106` for a file that measures `326`/`214`/`112`, and the pin-side
    prose described `:3778` as the `MERGE … SET n += $props` (that statement is the pin's `:3732`; `:3778`
    is `for prop in sorted(STORED_VECTOR_PROPS):`) and `:2755` as `placement_spellings()` (that function is
    the pin's `:1798`; `:2755` is `if k not in tail_denied})`). Nothing asserted them, so both could revert
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
        "the pin's `:3778` is",
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
        # `cache_projection.py:2485`, a `main`-set address, so the numerator moves to 124 from 123), and "±5"
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
    """Negative control — the exact defect: the pre-fix `+3` pointed the loop's cite at the `#7704` note.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).

    The spurious line is DERIVED FROM CONTENT, not hardcoded: the note it must land on has moved twice
    since this control was written, and a hardcoded `4907` rotted into a line that no longer holds the
    note — the do-nothing control then failed for the wrong reason (the note check, not the content arm).
    """
    loop_line = 4906
    note_line = next(
        i + 1
        for i, line in enumerate((REPO / _SE).read_text(encoding="utf-8").splitlines())
        if "#7704 (MEASURED MECHANISM" in line
    )
    assert note_line > loop_line, "the `#7704` note must sit BELOW the loop it annotates"
    row = (_M3_BRIEF, "tortoise/search_engine.py:4921", _SE, loop_line, "for rank, (pid, _score) in enumerate(ranked)")
    doubled = (row[0], row[1], row[2], note_line, row[4])
    errors = _resolve((doubled,))
    assert errors, "a spurious +3 must red the resolver"
    assert f":{note_line}" in errors[0] and "#7704" in errors[0]
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
    assert docs == {_AUDIT, _SUBSTRATE, _M3_BRIEF, _CENSUS, _EVIDENCE, _INDEX}
    assert len(_PINS) == 50, (
        "47 on this branch, +1 from `origin/main`'s own `:6938` row (the `#13489` sweep added it after this "
        "branch forked), +2 for the `#13345` review's D-1 dedup filter addresses (one per document that "
        "promises to carry the same number). The bound is not a target: a row DELETED to make the suite "
        "pass is the failure mode, so this number moves only when a row is added on purpose — the `:6938` "
        "row was added because §2.5 row 8's `(\"Object\", [\"name\"])` cite was the one address in that "
        "block no pin reached (it kept the pre-merge `:6869` while its four siblings were re-pointed)."
    )


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
    open at `main` and are named in neither list — `cache_projection.py` `:2485`, `:2542`, `:2714-2721`,
    `:2714-2715`, `:2703-2705`, `:2755`, `:3778` and `projection/entities.py`:5666. `:3778` is a
    `MERGE (n:…) SET n += $props` at the pin and the `cache_tier` arm at `main`, so a reader who followed
    the rule opened the pin and landed on a statement their row does not quote: "cite rotated, reader
    confidently opens the wrong line" — this document's own failure class, inverted.

    Two arms, because the defect was two-sided:

    * CONTENT — each of the eight carries, **on this branch's tree** (the tree this test reads),
      the fragment the document quotes for it. `_PINS` reaches five of them; `:2464`, `:2521` and
      `projection/entities.py:5666` were in NO pin before this one. ⛔ The earlier form of this
      bullet justified the same rows as "at `main` (the working tree: this branch is docs-only, so
      `main`'s `.py` files are this tree's)" — that was true when it was written and is FALSE for
      the rows `#13363` re-derived: `sdk.py` moved `+22` and `hosted_api.py` `+6`/`+18`, so those
      rows carry THIS tree's figures, not `main`'s. A row here is a claim about `REPO / path`, never
      about `main`'s copy of it — the same thing `_MAIN_SET_MISSED`'s own comment says.
    * CLAIM — the header still NAMES them, and no longer states the three absolutes the review measured
      false, nor the "pin-written remainder" label whose denominator contradicted it.

    NON-VACUOUS on the pre-fix document (`git show 295c26ebc8:<the file>`): the header region — every
    line before `## 1.` — carries **0** occurrences of `cache_projection` and **1** of each of the three
    absolutes, so every assertion below reds there. The region bound is what makes the token arm
    discriminating: `:2485`, `:2542` and `:2755` DO appear later in the document (row 13, §2.5), so an
    unbounded `in text` check would pass on the broken file.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    header = text.split("## 1. The population", 1)[0]

    rows = tuple((_AUDIT, token, path, line, fragment) for token, path, line, fragment in _MAIN_SET_MISSED)
    errors = _resolve(rows)
    assert not errors, "\n".join(errors)

    assert "cache_projection.py" in header, (
        "the header's `main` set names no `cache_projection.py` address, but that module carries eight "
        "cites in this document that open at `main` and NOT at the pin (the pin's `:3778` is "
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
