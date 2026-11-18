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
    # ⚠️ The token above carries its TABLE context on purpose. A bare `search_engine.py:4725-4727` is a
    # SUBSTRING of §2.1's verdict token (`` `none`: `search_engine.py:4725-4727` ``), so the verdict row
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
    (_AUDIT, "`sdk.py:37369`", _SDK, 37369, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    (_AUDIT, "`:37392`", _SDK, 37392, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    # ⛔ The three rows below pin SITES the pair rows above cannot reach, because those rows carry
    # TABLE/ROW context on purpose (a bare `:37392` is satisfied by the header enumeration or by the
    # proximity paragraph, so the §2.5 rows could be re-pointed alone with nothing red). MEASURED, at
    # the head before this pin: §2.5's adjacency row and row 8 both cited `sdk.py:37206` for the
    # `-[r]->(n)` walk — a line that is `bd_id: bd.confidence_mean` at this head, a document line at
    # the pin's neighbours and `RETURN type(r)…` on `:37392` — so the walk's ADDRESS resolved on NO
    # head the row named, while `len(_PINS)` and every count arm stayed green. The proximity
    # paragraph above §2.5 carried the same rot in the other direction: it named `:37183` (a `#11702`
    # comment at every head) as the address "the row carries" — the one thing that paragraph says
    # must never disagree with the row.
    (_AUDIT, "| `tortoise/sdk.py:37392` |", _SDK, 37392, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "the `-[r]->` walk (`sdk.py:37392`)", _SDK, 37392, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "they are `:37369` and", _SDK, 37369, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    # ⛔ The FTS leg's `entity_type="event"` cite, pinned at the site because the row's other numbers
    # (`:3005`, `:6933`) were pinned and this one was not. MEASURED: `sdk.py:57090` — the value the
    # `#13551` lane's previous pass re-derived on the PRE-merge tree — is a docstring line at this
    # head, at `f07b73b1de` and on `origin/main`, where the statement is `:57276` (the `#13550` /
    # `#13584` integration inserted 186 lines above it). A cite re-derived before a merge is stale
    # after it, which is D5's own rule; the pin is what makes the next one red instead of silent.
    (_AUDIT, '`sdk.py:57276` passes `"event"`', _SDK, 57276, 'entity_type="event"'),
    (_AUDIT, "`audit.py:817`", _AU, 817, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2452`", _AU, 2452, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2463`", _AU, 2463, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`mcp_server.py:5137`", _MCP, 5137, "entity_type=entity_type"),
    (_AUDIT, "`:5577`", _MCP, 5577, "entity_type=entity_type"),
    (_AUDIT, "`:8129`", _MCP, 8129, "entity_type=entity_type"),
    (_AUDIT, "`:6923-6939`", _PI, 6923, "_FULLTEXT_INDEX_LABEL_FIELDS"),
    (_AUDIT, "`:6933`", _PI, 6933, '("Event", ["subject", "name"])'),
    (_AUDIT, "`:6934`", _PI, 6934, '("Subject", ["name"])'),
    # ⛔ The Object half of the same declaration had NO row, and that absence is ISSUE 1 of the
    # `#13489` review: §2.5's header cited `:6869`, which `:6933`/`:6934`/`:6939` had left 64/65/70
    # lines behind — and `:6938` is the `("Object", ["name"])` row that had no pin at all, so the
    # one sibling a reader would actually chase was the uncovered one. An absent pin is not
    # coverage; the header now names it beside its three siblings and this row resolves it.
    (_AUDIT, "`:6938`", _PI, 6938, '("Object", ["name"])'),
    (_AUDIT, "`:6939`", _PI, 6939, '("Source", ["_searchText"])'),
    (_AUDIT, "record_serve.py:468", _RS, 468, "READ_POINT_BY_ID: AnchoredRead("),
    (_AUDIT, "goal_judgement.py:1128", _GJ, 1128, "where = \"MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator\""),
    (_AUDIT, "sdk.py:24112", _SDK, 24112, "(n:Point {id:$id})-[:TAGGED]->()"),
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
    # --- docs/evidence/13083-capability-census/README.md
    (_CENSUS, "cache_projection.py:2776", _CP, 2776, "tail_denied = forbidden | {"),
    # ⚠️ Row 13b's Source-writer cite, added because the site had NO pin at all — the same shape
    # the `:6938` row above was added for. It sat at `:19637` (a comment about provenance loss in
    # the `sessionCaptured` stamp block) for merges without a red, because
    # `rg -n '19637' docs/ tests/ tools/ config/` reached this one row and no `_PINS` /
    # `_TOKEN_OCCURRENCES` entry covered it. ⛔ `#13486` moved the writer under this row, so it is
    # re-derived here to the line that actually carries `search_text = _source_search_text(`
    # (`grep -n 'search_text = _source_search_text(topics=topics)' tortoise/sdk.py`), not carried.
    (_AUDIT, 'sdk.py:21756', _SDK, 21756, 'search_text = _source_search_text(topics=topics)'),
    (_AUDIT, 'sdk.py:21798', 'tortoise/sdk.py', 21798, 's.summary=CASE WHEN $sum'),
    (_AUDIT, '`:21921`', 'tortoise/sdk.py', 21921, 's.summary, s.topics, s.'),
    (_AUDIT, 'hosted_api.py:22925', 'tortoise/hosted_api.py', 22925, 'MERGE (s:Source {url:$did})'),
    (_AUDIT, '`:22737`', 'tortoise/hosted_api.py', 22737, 'def _execute_commit_writes'),
    (_AUDIT, '`:33601`', 'tortoise/hosted_api.py', 33601, 'MERGE (p:PackInstall {namespace: $ns})'),
    # --- the four corrections this pass made that NO row reached (review cycle 3, F5). Each token
    # carries its own wording, not a bare number, because a bare `:NNNN` is satisfied by any sibling
    # spelling of the same digits — the defect `test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence`
    # exists for, and the reason row 13's parenthetical and §2.1's count marker survived three passes.
    (_AUDIT, '`:5310` at this head', 'tortoise/projection/entities.py', 5310, 'def _upsert_document(self, ev: dict) -> None:'),
    (_AUDIT, '`:7061` at this head', 'tortoise/projection/entities.py', 7061, 'def _upsert_source('),
    # ⚠️ Widened from the bare `` `:6681` `` this pass first wrote (review cycle 6, F5): a bare `:NNNN`
    # is exactly the shape the comment above says is excluded — it is satisfied by any sibling spelling
    # of the same digits — and this row was the one of the four that carried it.
    (_AUDIT, '`:6681` inside the same function', _SE, 6681, 'if expand_ops:'),
    (_AUDIT, '`:3005` derives the label', _SE, 3005, 'label = "Source" if entity_type =='),
    # --- three re-derived addresses that had NO pin at all, so a `+1` mutation left 441 tests green
    # (review cycle 6, P2-2). `grep -rn '28008\|28372' tests/ tools/` returned nothing before these rows:
    # the guard set is per-token, and an unlisted token is coverage the file only APPEARED to have.
    (_INDEX, '`sdk.py:28067` at this head', _SDK, 28067, 'WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL'),
    (_EVIDENCE, '(`sdk.py:28067`), so any non-NULL', _SDK, 28067, 'WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL'),
    (_EVIDENCE, 'called at `sdk.py:28431`', _SDK, 28431, 'result = apply_entity_merge('),
)

#: The `main`-set group the `#13124` review found OUTSIDE the header's `main` enumeration: (token the
#: header must carry, target file, 1-based line **on this branch's tree**, verbatim fragment on that
#: line). Five of these
#: are reached by `_PINS` already; `:2464`, `:2521` and `projection/entities.py:5666` were in NO pin at all
#: before this one, and the header named none of the eight — which is the defect
#: `test_the_main_set_enumeration_names_the_group_it_was_missing` exists for.
#: ⚠️ The sdk/hosted_api rows here were `main`'s figures until `#13363` re-derived them: that branch's
#: own inserts move `sdk.py` by +22 and `hosted_api.py` by +6, so the line is THIS tree's and the header
#: records the delta and main's figure beside it. A row here is a claim about a line in the tree the
#: test reads (`REPO / path`), never about `main`'s copy of it.
_ENT = "tortoise/projection/entities.py"
_MAIN_SET_MISSED: tuple[tuple[str, str, int, str], ...] = (
    (":2485", _CP, 2485, "def _episodic_source_search_text_is_the_lexicon"),
    (":2542", _CP, 2542, 'return summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"'),
    (":2714", _CP, 2714, "it is the ONLY field the Source FTS index"),
    (":2714-2715", _CP, 2715, "un-indexes a restored session"),
    (":2724-2726", _CP, 2724, "prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS"),
    (":2776", _CP, 2776, "tail_denied = forbidden | {"),
    (":3841", _CP, 3841, 'if table == "point"'),
    (":5666", _ENT, 5666, "s.summary=coalesce($summary, s.summary, '')"),
    # Round 8: written at `main`, pinned at `main`, but absent from the enumeration — so the header's rule
    # sent readers to the pin. ⛔ NOT "where every one is a comment": at the pin `sdk.py:23982` is a DELETE
    # statement and `hosted_api.py:22919` is `if is_subject_kind(...)`, both executable. The pin is wrong for
    # all six; it is a comment for most, and unrelated CODE for the two that matter most here.
    ("sdk.py:24112", _SDK, 24112, "(n:Point {id:$id})-[:TAGGED]->()"),
    ("search_engine.py:6738", _SE, 6738, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    ("sdk.py:21798", _SDK, 21798, "s.summary=CASE WHEN $sum"),
    (":21921", _SDK, 21921, "s.summary, s.topics, s."),
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
#:
#: ⚠️ Every key must appear EXACTLY ONCE. Python keeps the LAST duplicate, so a repeated key silently
#: discards the count declared earlier in this literal — and because what survives IS a count, the test
#: below passes on a tree whose reviewed value is gone (measured: green on the tree `#13345` landed
#: three duplicated keys on). `ruff`'s F601 is the only instrument that sees a duplicate, and it runs on
#: BOTH events — the PR event (`ci.yml`) and the push to `main` (`main-lint.yml`, #2174). Neither gated
#: the merge that landed the duplicate: on `#13345` the PR-event `lint` RED on the PR's own sha but its
#: run STARTED 11 min after the merge (18:31:33Z vs 18:20:40Z), and the push-to-`main` gate that first
#: reported (18:24:17Z on `549bad0c77`) is post-merge by construction. That is why `main` was lint-red
#: until this removal. #9314 is the pytest-level guard this file does not have.
_TOKEN_OCCURRENCES: dict[tuple[str, str], int] = {
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:3841'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4797-4801'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:6684'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7043'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7045'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7042-7045`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7048-7061`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3100'): 5,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '| `tortoise/search_engine.py:4725-4727` |'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4140'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3005'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3072`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2712-2723'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2724-2726'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2714-2715`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2776'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2882`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:37369`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:37392`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '| `tortoise/sdk.py:37392` |'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'the `-[r]->` walk (`sdk.py:37392`)'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'they are `:37369` and'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:57276` passes `"event"`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`audit.py:817`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2452`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2463`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`mcp_server.py:5137`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5577`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:8129`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6923-6939`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6933`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6934`'): 1,
    # 2: `:6938` is BOTH the §1 label-scan table row's own key (already in this document) and the
    # §2.5 header enumeration's disclosure of THIS tree's Object declaration, added by `#13489` beside
    # its three siblings. Two sites, one spelling — a single pin resolves the line, and the count is
    # what stops a third occurrence appearing unannounced. Measured at THIS head:
    # `grep -o '\`:6938\`' docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | wc -l` -> 2, and
    # `grep -o '\`:6939\`' …` -> 1, so `origin/main`'s 1 is the count BEFORE `#13489`'s second site.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6938`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6939`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'record_serve.py:468'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'goal_judgement.py:1128'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:24112'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:6738'): 2,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', 'tortoise/search_engine.py:3451'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:3449`'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:2888'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'search_engine.py:2730'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:4921'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', '`search_engine.py:4840`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'search_engine.py:3081-3096'): 1,
    # ⛔ `origin/main`'s two `sdk.py:28019` rows are DROPPED at this merge, not carried: `28019` is
    # `main`'s value for the content-review filter, and this branch re-derived that cite to `:28067`
    # in BOTH documents. Measured at this head: `grep -rn 'sdk.py:28019' docs/00_index.md
    # docs/architecture/ENTITY-VERDICT-BANDS.md` -> no match (the token survives only in
    # `05-verify.md`'s sentence stating the re-point), so a row asserting 1 occurrence would be a
    # false pin over a token the documents no longer carry.
    ('docs/evidence/13083-capability-census/README.md', 'cache_projection.py:2776'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21756'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21798'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:21921`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:22925'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:22737`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:33601`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5310` at this head'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7061` at this head'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6681` inside the same function'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3005` derives the label'): 1,
    ('docs/00_index.md', '`sdk.py:28067` at this head'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', '(`sdk.py:28067`), so any non-NULL'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'called at `sdk.py:28431`'): 1,
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
    the counts still read `322`/`216`/`106` for a file that measured `326`/`214`/`112` at that review (`326`/`215`/
    `111`/`123` at the head that re-measured them), and the pin-side
    prose described `:3778` as the `MERGE … SET n += $props` (that statement is the pin's `:3732`; the
    `for prop in sorted(STORED_VECTOR_PROPS):` line is the pin's `:3735`, and `:3778` names neither — ⛔
    measured with `grep -n` on `ccb49741b2ec`, because the `:3778` in this sentence is the address the
    prose used to name, not the statement it claimed sat there) and `:2755` as `placement_spellings()`
    (that function is the pin's `:1798`; `:2755` is `if k not in tail_denied})`). Nothing asserted them, so
    both could revert silently — that is what this test stops.

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
        # `cache_projection.py:2464`, a `main`-set address, so the numerator moves to 124 from 123), and "±5"
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
    """Negative control — the exact defect: the loop's cite re-pointed at the `#7704` note below it.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).

    The spurious line is DERIVED FROM CONTENT, not hardcoded: the note it must land on has moved twice
    since this control was written, and a hardcoded `4907` rotted into a line that no longer holds the
    note — the do-nothing control then failed for the wrong reason (the note check, not the content arm).
    """
    loop_line = 4921
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
    # 48: `#13489`'s review added the `:6938` row (`("Object", ["name"])`, the §2.5 address that
    # had NO pin at all, which is how `:6869` stayed behind its three siblings — `:6933`/`:6934`/`:6939`
    # sit 64/65/70 lines below `:6869`, while `:6938`, the row that had no pin, is the 69th), and row
    # 13b's Source-writer pin (`sdk.py:21692` — `:21648` before `#13486` moved the writer) is the
    # same shape added again, for the same reason.
    # 48 -> 52 at review cycle 3: four rows added for corrections whose sites had NO pin at all
    # (row 13's `entities.py` pair, §2.1's count marker, the Event row's label-derivation cite).
    # 52 -> 55 at review cycle 6: the three addresses review P2-2 measured as pinned NOWHERE —
    # `sdk.py:28008` in `00_index.md` and in `ENTITY-VERDICT-BANDS.md`, and `sdk.py:28372` there too —
    # where a `+1` mutation on all three left 441 tests green. Same shape, same reason.
    # 55 -> 58 at the #13551 fix lane: the three §2.5 sites that cited `sdk.py:37206` (or, in the
    # proximity paragraph, `:37183`) for statements that are on `:37392` / `:37369` at every head the
    # row names — measured above the rows. The pair pins could not see them: a `:37392` anywhere on
    # the line satisfied the count arm, which is the same one-site-vs-one-string gap `:6938` was.
    # 58 -> 59 in the same lane: the FTS row's `entity_type="event"` cite, re-derived BEFORE the
    # `origin/main` integration and therefore 186 lines stale at the merge head (`:57090` -> `:57276`).
    # The arm is an anti-SHRINK arm, so it moves with a documented ADDITION, never with a deletion.
    assert len(_PINS) == 59


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
    # ⛔ The SUBJECT PROBE's four, added at review cycle 3: this pass delta-shifted `:6716-6729` to
    # `:6718-6731` and NOTHING red, because this arm's tuple stopped at the three `search_engine.py`
    # addresses. A recorded-wrong address takes no offset at any head, so the four are asserted as a
    # set and the shifted form is asserted absent.
    assert "(`:6711`, `:6710-6713`, `:6713`, `:6716-6729`)" in text, (
        "the paragraph that records which addresses were WRONG no longer names the subject probe's "
        "four (`:6711`, `:6710-6713`, `:6713`, `:6716-6729`) — they record what the address WAS, so "
        "they take no delta and re-deriving them by content is not possible."
    )
    for shifted_probe in ("`:6718-6731`",):
        assert shifted_probe not in text, (
            f"the recorded-wrong list carries `{shifted_probe}`, an address no tree ever carried — it "
            "is `:6716-6729` plus the `search_engine.py` +2, i.e. a delta applied to a RECORD rather "
            "than to a citation."
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
    count(*)`, not the `run_structural_query` scan the table row quotes at `:4725-4727`. Measured on
    `origin/main` at that re-cut: the table row named `:4708-4710`, the verdict paragraph named
    `:4489-4490` — two addresses for one reader, in one table, again. The `:4350-4351` assertion cannot see
    it (`origin/main` reads 0 there), so this test would have passed over the live defect; the
    `:4489-4490` assertion is what makes it reachable.

    ⛔ **AND IT RETURNED A THIRD TIME, WHICH IS WHY THE LAST ARM AGREES THE TWO SPELLINGS INSTEAD OF
    ASSERTING ONE LITERAL — the measured witness that a literal cannot see an agreement.** `#13466`
    re-derived §2.1's TABLE row to `:4725-4727` and left this verdict paragraph at the pre-move
    `:4708-4710`, so `main` again gave TWO addresses for one reader (`:4723` is
    `where_clause = " AND ".join(conditions)`, the statement BEFORE the scan; `:4708-4710` there is the
    comment block that justifies the kind clause). This test's first
    assertion — a literal bound to the verdict's own number — stayed GREEN over it, because a pin that
    names one side of a two-sided claim has nothing to disagree with. The pair is now read out of the
    document and compared; the literal is kept only as the content-verified value at this head.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    assert "`none`: `search_engine.py:4725-4727`, a plain label scan" in text, (
        "§2.1's verdict paragraph no longer names the label scan at `:4725-4727`; re-derive it from "
        "the statement rather than applying a delta to the address beside it."
    )
    assert "`none`: `search_engine.py:4350-4351`" not in text, (
        "§2.1's verdict paragraph names the label scan at `:4350-4351` while the table above it names "
        "the same reader row at `:4725-4727` — two addresses for one reader, and only one of them can "
        "open at any head. That is the defect class this file exists for."
    )
    assert "`none`: `search_engine.py:4489-4490`" not in text, (
        "§2.1's verdict paragraph names `:4489-4490`, which is `run_vector_query`'s "
        "`MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(*)` — a different plain label "
        "scan from the `run_structural_query` one the table row quotes at `:4725-4727`. `#13290` reached "
        "it by applying a delta to `:4350-4351` instead of opening the statement, which is the same "
        "offset-not-content failure as the `:4549` re-point this file was written for. Re-derive from "
        "the fragment (`grep -n 'MATCH (n:{label_str})'`), never from the address beside it."
    )
    # ⛔ THE AGREEMENT ARM. §2.1's verdict paragraph and its `run_structural_query` table row name the
    # SAME reader, so their ranges must BE the same range — read from the document, not restated.
    # `#13466` is the measured witness that a literal on one side cannot see the other: it moved the
    # table and left the verdict behind, and the assertion above (bound to the verdict's own literal)
    # stayed green over a document that named two addresses for one reader.
    verdict = re.search(r"`none`: `search_engine\.py:(\d+)-(\d+)`, a plain label scan", text)
    table = re.search(
        r"\| `tortoise/search_engine\.py:(\d+)-(\d+)` \| `run_structural_query`'s", text)
    assert verdict and table, (
        "§2.1 no longer spells the label scan as a range in BOTH the verdict paragraph and the "
        "`run_structural_query` table row; this arm compares the two, so both must stay ranges "
        f"(verdict={verdict and verdict.group(0)!r}, table={table and table.group(0)!r})"
    )
    assert verdict.groups() == table.groups(), (
        f"§2.1 gives the label scan two addresses — the verdict paragraph says "
        f":{verdict.group(1)}-{verdict.group(2)}, the table row says "
        f":{table.group(1)}-{table.group(2)} — and only one of them can open at any head. Re-derive "
        "BOTH from the fragment (`grep -n 'MATCH (n:{label_str})'`); a delta applied to one of them "
        "is how this defect has now landed three times."
    )


def test_the_main_set_enumeration_names_the_group_it_was_missing() -> None:
    """The header's reader rule was an ABSOLUTE, and the absolute was false.

    `#13124`'s header said the enumeration was "the WHOLE of the `main` set" and that every other
    `file:line` "is at the pin `ccb49741b2ec`". The `#13124` REVIEW measured both false: eight addresses
    open at `main` and are named in neither list — `cache_projection.py` `:2464`, `:2521`, `:2693-2660`,
    `:2714-2715`, `:2724-2726`, `:2776`, `:3841` and `projection/entities.py`:5666. `:3778` is a
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
    discriminating: `:2464`, `:2521` and `:2755` DO appear later in the document (row 13, §2.5), so an
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
    # ⛔ THE WHOLE ENUMERATION, not one entry (review cycle 3, F1). `#13466` re-pointed FOUR of its
    # entries to working-tree values and NOTHING red, because this arm asserted only the Object entry.
    # ⚠️ This comment said "three" while the failure message below said "FOUR" (review cycle 5): the
    # measured set is `:7025-7028`, `:7031-7044`, `:3070` and `:2877`->`:2882` — `git diff b64290529b^
    # b64290529b -- docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md` prints exactly those four moves.
    # ⚠️ `:2876` is that entry's address at `ae54df166d` (the value the loop below pins), NOT the value
    # that commit moved: the header already read `:2877` at `b64290529b^`, one line stale before it was
    # touched — which is this file's own thesis, so the two numbers are named with their heads instead
    # of one token standing for both (review cycle 6, P3).
    # Each value below is measured AT `ae54df166d` — `git show ae54df166d:tortoise/search_engine.py |
    # grep -nF '<fragment>'` — and each is the reading this arm protects. The dual-head form for the one
    # entry that had drifted at EVERY pass (`sdk.py`'s second `capture_redactions` writer) is the shape
    # the docstring above sanctions: the declared head's figure first, this tree's after it in parens.
    for bound in (
        "subject probe `:7025-7028`",
        "`subject_binding_available()` at `:7031-7044`",
        "index note `:3070`;",
        "`capture_redactions` writer `:2876` (on this tree `:2882`)",
        "walk `:4780-4784`",
        "operator leg `:6667`",
        "label scan `:4708-4710`",
        "HNSW\n`:4123`",
        "label derivation `:3003`",
        "`:3098`,",
        # ⛔ ADDED after the `#13345` main-merge re-pointed THIS entry to the merged tree's `:5577`
        # while the list it sits in opens at `ae54df166d` (review round 8): the arm had no `mcp_server`
        # bound at all, so that single re-point went green — the same class the entry counts above
        # exist to stop, one file over. The value here is the declared head's; this tree's is disclosed
        # beside it in the document, so the bound reads the declared-head spelling and only that.
        "`mcp_server.py`'s `entity_type` passthrough `:5529`",
    ):
        assert bound in text, (
            f"the header enumeration no longer carries {bound!r} — those are its addresses AT `main` "
            "`ae54df166d`, the head the paragraph announces twice. A working-tree value here re-points a "
            "history cite, the `⚠️ CORRECTED` failure this file exists to stop (`#13466` did exactly that "
            "to FOUR of these entries — the probe range, the binding range, the index note and the writer — "
            "and nothing red). Re-derive at `ae54df166d`, never at the tree."
        )
    assert '`("Object", ["name"])`\n`:6869`;' in text, (
        "the header enumeration no longer names the Object declaration at `:6869` — the address it holds "
        "at the `main` head that paragraph announces (`ae54df166d`). `:6938` is THIS tree's value (and "
        "§2.5's row); putting it here re-points a history cite to a working-tree address, the "
        "`⚠️ CORRECTED` failure this file exists to stop. Restore `:6869` directly above a `;`. To ALSO "
        "disclose this tree's figure, keep the declaration where it is, append the disclosure AFTER the "
        "semicolon, and bump `_TOKEN_OCCURRENCES[':6938']` to 2 in the same change — otherwise "
        "`test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence` reds. Do not loosen either arm."
    )


#: ⛔ THE `main`-HEAD FIGURE COUNT GUARD IS GONE, and its removal is recorded rather than silent.
#: This revision used to carry `_MAIN_HEAD_FIGURES` — twelve addresses AT `ae54df166d` written BESIDE
#: this tree's figure for the same site, with a `len(...) == 12` anti-shrink arm. That guard existed
#: because the branch could not resolve `main`'s own re-derivation, so the header kept a dual-head
#: narrative. `#13486` (`0186df59`) has since landed that re-derivation on `main` at `main`'s
#: numbering, and the merged header now states ONE set of figures — every one of them this tree's,
#: each resolved by a `_PINS` row above. With no `main`-head figure left in the document the dict had
#: an EMPTY population, so keeping the arm would have meant re-manufacturing the narrative it guarded.
#: Deleting it therefore removes no coverage that has a subject: the figures it counted (`:6854-6870`,
#: `:36985`, `:37008`, `:23982`, `:21668`, `:21791`, `:22919`, `:22731`, `:33583`) no longer appear in
#: the header, and the addresses that REPLACED them (`:6923-6939`, `:37369`/`:37392`, `:24048`,
#: `:21734`/`:21857`) are pinned to their content by `_PINS`, which is the stronger arm — a pin
#: resolves the line, a count only counts a spelling. #13508 still owns the head-selecting arm for a
#: figure that has no working-tree line.


def test_the_13102_addendum_states_the_consume_pair_at_this_head() -> None:
    """The D5 row's `#13102` addendum states a PAIR in the present tense, and nothing read it.

    Round-3 review, P1: the addendum said *"moved the `subject_binding_available` consume
    pair to `:12111`/`:12164` here"* while the SAME markdown LINE — three clauses earlier —
    said `:12116`/`:12169`, which is what the pin file asserted at that head and what the tree held:

        grep -nF 'subject_binding_available,' tortoise/sdk.py     -> 12136
        grep -nF 'and not subject_binding_available(graph):' …    -> 12189
        sed -n '12111p' tortoise/sdk.py -> return results

    `:12111`/`:12164` is `#13456`'s pre-`#13466` value, so the cell quoted two heads as
    one. ⛔ **Why no existing row saw it**: the capstone pin's `_pin_row_failures` scopes a
    row to ONE markdown LINE, and D5 is one line carrying ~50 numbers — so any `:12111`
    anywhere on it satisfied every `sdk`-keyed row, and the addendum's own contradictory
    spelling was read by nothing. This arm is scoped to the addendum's SENTENCE, which is
    the narrowest thing that can disagree with it.
    """
    text = (REPO / "docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md").read_text(
        encoding="utf-8")
    addendum = re.search(
        r"moved the `subject_binding_available` consume pair to `:(\d+)`/`:(\d+)` here", text)
    assert addendum, (
        "the D5 row's `#13102` addendum no longer states the `subject_binding_available` "
        "consume pair as `:<n>`/`:<n>` — this arm reads that sentence, so if it was "
        "reworded, re-scope this regex rather than deleting the arm")
    sdk = (REPO / _SDK).read_text(encoding="utf-8").splitlines()
    # `strict=True`: the regex has exactly two groups and the tuple exactly two needles, so a
    # mismatch is a defect in this arm, not a shorter iteration to be tolerated (ruff B905 red at
    # `46d7eddbc8` — the lane that landed this guard with a bare `zip` left `ruff check .` failing).
    for n, needle in zip(addendum.groups(),
                         ("subject_binding_available,",
                          "and not subject_binding_available(graph):"),
                         strict=True):
        assert sdk[int(n) - 1].strip() == needle, (
            f"the addendum's consume pair cites sdk.py:{n}, which reads "
            f"{sdk[int(n) - 1].strip()!r}, not {needle!r} — re-derive the pair by content "
            f"(`grep -nF '{needle}' tortoise/sdk.py`), never by a delta")
