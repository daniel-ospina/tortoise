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
#: `#14087` review round 7: `00_index.md`'s entity-verdict row points at this file's `:43` in the
#: PRESENT tense ("the bullet is `entity_verdict.py:43`"), so the address is head-relative.
_EV = "tortoise/entity_verdict.py"
_MCP = "tortoise/mcp_server.py"
_RS = "tortoise/record_serve.py"
_GJ = "tortoise/goal_judgement.py"
_HB = "tortoise/hosted_backup.py"
_SSM = "tortoise/session_source_prose_migration.py"
#: `#13854`'s vector-index-parity plan. Its reader table cites `hosted_backup.py:2683` for
#: `_index_has_is_operator()`, and that row is in this branch's diff.
_PLAN_4997 = "docs/plans/2026-09-25-4997-vector-index-parity.md"

#: (document, cite token the document must still carry, target file, 1-based line, fragment at that line)
_PINS: tuple[tuple[str, str, str, int, str], ...] = (
    # --- docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md (search_engine.py +3, cache_projection.py +2)
    (_AUDIT, "cache_projection.py:3841", _CP, 3841, 'if table == "point"'),
    (_AUDIT, "search_engine.py:4950-4954", _SE, 4954, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)"),
    (_AUDIT, "search_engine.py:7153", _SE, 7153, "(op)-[r2:IMPL|hasPart]-(other:Point)"),
    (_AUDIT, "search_engine.py:7560", _SE, 7560, "(n:Point)-[r:aboutSubject]->(:Subject)"),
    (_AUDIT, "search_engine.py:7562", _SE, 7562, "(m:Event)-[r2:aboutSubject]->(:Subject)"),
    # Row 149's TWO BARE range cites carry no `search_engine.py` prefix, so the token-level pins for
    # `6659`/`6661` above do not reach them — they are the only shifted mentions in this document the
    # first pin set missed. Both were already correct at the pre-fix head, so these two are
    # COMPLETENESS pins: they catch the re-point/double-shift that DID happen in this class (`:4549`
    # re-pointed to `:4552`), which is the failure the whole file exists for.
    (_AUDIT, "`:7559-7562`", _SE, 7559, "_SUBJECT_SOURCE_SCOPED_PROBE = ("),
    (_AUDIT, "`:7565-7578`", _SE, 7565, "def subject_binding_available"),
    (_AUDIT, "search_engine.py:3157", _SE, 3157, "CALL db.idx.fulltext.queryNodes"),
    (_AUDIT, "| `tortoise/search_engine.py:4878-4880` |", _SE, 4878, "MATCH (n:{label_str})"),
    # ⚠️ The token above carries its TABLE context on purpose. A bare `search_engine.py:4878-4880` is a
    # SUBSTRING of §2.1's verdict token (`` `none`: `search_engine.py:4878-4880` ``), so the verdict row
    # satisfies this one and the TABLE row can be re-pointed alone without reddening anything — measured:
    # mutate the table to `:4511-4513` with the bare token -> 0 red, with the table-context token -> 1 red.
    (_AUDIT, "search_engine.py:4293", _SE, 4293, "CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit"),
    (_AUDIT, "search_engine.py:3062", _SE, 3062, 'label = "Source" if entity_type == "document"'),
    (_AUDIT, "`:3129`", _SE, 3129, "the fulltext index is on Source._searchText"),
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
    (_AUDIT, "`:2921`", _SDK, 2921, "MATCH (s:Session {id:$sid}) SET s.capture_redactions=$redactions"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`sdk.py:37883`", "tortoise/sdk.py", 37883, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`:37906`", "tortoise/sdk.py", 37906, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    # ⛔ The three rows below pin SITES the pair rows above cannot reach, because those rows carry
    # TABLE/ROW context on purpose (a bare `:37467` is satisfied by the header enumeration or by the
    # proximity paragraph, so the §2.5 rows could be re-pointed alone with nothing red). MEASURED, at
    # the head before this pin: §2.5's adjacency row and row 8 both cited `sdk.py:37283` for the
    # `-[r]->(n)` walk — a line that is `bd_id: bd.confidence_mean` at this head, a document line at
    # the pin's neighbours and `RETURN type(r)…` on `:37467` — so the walk's ADDRESS resolved on NO
    # head the row named, while `len(_PINS)` and every count arm stayed green. The proximity
    # paragraph above §2.5 carried the same rot in the other direction: it named `:37183` (a `#11702`
    # comment at every head) as the address "the row carries" — the one thing that paragraph says
    # must never disagree with the row.
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "| `tortoise/sdk.py:37906` |", "tortoise/sdk.py", 37906, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "the `-[r]->` walk (`sdk.py:37906`)", "tortoise/sdk.py", 37906, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "they are `:37883` and", "tortoise/sdk.py", 37883, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    # ⛔ The FTS leg's `entity_type="event"` cite, pinned at the site because the row's other numbers
    # (`:3062`, `:6984`) were pinned and this one was not. MEASURED: `sdk.py:57209` — the value the
    # `#13551` lane's previous pass re-derived on the PRE-merge tree — is NOT the statement at this
    # head (at this head `:57090` is a BLANK line inside `_no_source_advisory`'s docstring, which
    # opens at `:57089`; `scored.append({` is `:57284`), nor on
    # `origin/main`, where the statement is `:57617` (the `#13550` / `#13584`
    # integration inserted 186 lines above it, later merges 147 more — 333 in all at the PRE-`#13833`
    # tip (`:57423`) — and `main`'s `#13833` a further 194, so 527 at `origin/main`). ⛔ AND EACH
    # HEAD GETS ITS OWN VALUE, because the arithmetic closes on a CHAIN, not on one figure:
    # `57090 + 186 = 57276` is `f07b73b1de`'s (the statement sits on `:57276` there, so `:57423` is
    # NOT that head's), and `57276 + 341 = 57617` is `origin/main`'s and this head's — the third hop
    # being `main`'s `#13833`, which inserted 194 above it (`57276 + 147 = 57423` is what this
    # comment carried before that landed, and it is HISTORY at this head, not a locator). Measured per
    # head with `git show <rev>:tortoise/sdk.py | grep -n 'entity_type="event"'` — an earlier cut of
    # this comment named `:57387` for BOTH heads and so contradicted its own 186 (#13750 review).
    # A cite re-derived before a merge is stale after it, which is D5's own rule; the pin is what
    # makes the next one red instead of silent.
    # ⚠️ This branch's own `#5425` seam wrap sits ABOVE this cite and is written LINE-COUNT NEUTRAL
    # for exactly that reason — a +1 there moved this pin (and every other `sdk.py` pin below
    # `:51496`) by one, measured at the merge resolution.
    # ⛔ The "comparable" denominator: this pair names a line past the pin's `sdk.py` (56,415 lines),
    # so the header counts it as drifted rather than compared, and it is ONE OF THREE that are
    # incomparable at the pin — measured at this head: 211 comparable, 119 of them drifted, plus
    # those three (`sdk.py:58099`, `search_engine.py:7196`, `:7198`) = 122 of the 214. ⛔ The
    # earlier form of this comment said "the seam above it is line-neutral, so nothing moved" and
    # carried `57276`: the seam wrap sits above the cite, and the statement has moved +341 since
    # the head this pin was written at (+147 to the pre-`#13833` tip, then `main`'s `#13833` a
    # further 194), so the token moves WITH the line — a pin whose token and
    # target disagree cannot red on the stale cite it exists to catch (#13750 VGATE).
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`sdk.py:58099` passes `\"event\"`", "tortoise/sdk.py", 58099, "entity_type=\"event\""),
    (_AUDIT, "`audit.py:817`", _AU, 817, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2452`", _AU, 2452, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2463`", _AU, 2463, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`mcp_server.py:5250`", _MCP, 5250, "entity_type=entity_type"),
    # ⛔ §2.5's `:Tag` traversal row cites this address and, until this row, NOTHING read it —
    # at that head `grep -rn 11281 tests/` returned nothing (it returns this file's own comments
    # now, which is not a read): the audit document's header promises a reader for every entry it
    # re-derives, and this one had none. The document cites the query LITERAL's
    # first line, not the `g.query(` call above it — at THIS head the call is `:11589` and the
    # literal `:11590`; at the head that row was written for they were `:11280` / `:11281`, and
    # `#14169` moved both together (its siblings in the same paragraph follow the same
    # convention — `sdk.py:31533` is the literal while `:31532` is the `rows = proj.g.query(` call).
    (_AUDIT, "`mcp_server.py:11590`", _MCP, 11590, "OPTIONAL MATCH (t)<-[:TAGGED]-(q)"),
    # ⛔ The SAME address has a SECOND written site — the header enumeration spells it BARE
    # (`` `mcp_server.py:5250`/`:8515`/`:11590` ``), and the §2.5 token above cannot see a bare
    # `:NNNN`. Review at `b9e7822362c6`, P2: reverting ONLY the header half (`:11281` -> `:11280`,
    # the address's spelling at that head) left every arm green — `_PINS` counts were unchanged
    # (63/63), `_TOKEN_OCCURRENCES`' key count
    # stayed 1 (the §2.5 spelling is a different key), `_REDEFINED_13854` was untouched and the
    # `_resolved_pairs` census was identical (223 pairs) — so the document could name two addresses
    # for one reader with nothing red. That is the false pass this file exists to stop, and it is the
    # `#13466` shape (four header entries re-pointed, nothing red). Its two siblings are pinned per
    # SITE for exactly this reason, and each carries a bare-token row with count 2 (header + §2.5):
    # `` `:8515` `` sits on the same five-address header trio as this row; `` `:5720` `` sits on the
    # earlier `entity_type` passthrough line. This row and its count are the site the re-derived
    # §2.5 row left behind.
    (_AUDIT, "`:11590`", _MCP, 11590, "OPTIONAL MATCH (t)<-[:TAGGED]-(q)"),
    # 3 -> 2 at the #14169 re-derivation (`b4da1f5c01`), which moved this address `+309`. The tally
    # was 3 while `#13991`'s own prose added two more BARE sites of it (the header trio, §2.5's
    # five-address enumeration, and §2.5's provenance sentence); after the move the provenance
    # sentence still reads the OLD number — it is the record of that correction, not a cite of this
    # head — and it is DELIBERATELY NOT counted here. The `mcp_server.py:11590` row above (count 1, the
    # row whose token carries the path rather than a bare `:NNNN`) covers only the §2.5 one-token
    # spelling; this row covers the bare ones.
    # 2 -> 4 at the #14246 fix lane: the header now names the address at TWO more bare sites — (a) the
    # comparability split's non-comparable enumeration, where `mcp_server.py`'s `:11590` is the
    # ELEVENTH pair that no longer has a line at the pin (so the split has to name it), and (b) §2.5's
    # head-declaration sentence, which states where it sits at the pin now that the pin's copy of the
    # file is shorter than the address. MEASURED on this document rather than carried:
    # `grep -o '`:11590`' docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | wc -l` -> 4 (the header trio,
    # the split's enumeration, §2.5's five-address enumeration, §2.5's head-declaration sentence);
    # `grep -n '`:11281`'` -> the provenance sentence and that head-declaration sentence, neither of
    # them counted here because neither is a cite of this head. Re-derive before trusting this: a line
    # number stated in prose drifts exactly like the citations this file pins.
    # Re-pointing either live spelling alone still reds here, which is the discrimination this row
    # exists for; the historical `:11281` is reached by no row, deliberately, because it is not a
    # claim about this head.
    (_AUDIT, "`:5720`", _MCP, 5720, "entity_type=entity_type"),
    (_AUDIT, "`:8515`", _MCP, 8515, "entity_type=entity_type"),
    # ⛔ The four `sdk.py` members of §2.5's `:Tag` row are enumerated in the header as BARE `:NNNN`
    # siblings — a spelling none of the rows above reaches (they all require a path or the one-token
    # form), which is the same one-site-vs-one-string gap the `:11281` header half and `:6989` were:
    # re-pointing any one of them to a stale number left every arm green. One ROW per SITE, pinned by
    # the fragment the header paragraph quotes for it.
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`:31551`", "tortoise/sdk.py", 31551, "MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag})"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`:31533`", "tortoise/sdk.py", 31533, "\"MATCH (t:Tag) \""),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`:24513`", "tortoise/sdk.py", 24513, "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) "),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`:24857`", "tortoise/sdk.py", 24857, "MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t"),
    (_AUDIT, "`:6974-6990`", _PI, 6974, "_FULLTEXT_INDEX_LABEL_FIELDS"),
    (_AUDIT, "`:6984`", _PI, 6984, '("Event", ["subject", "name"])'),
    (_AUDIT, "`:6985`", _PI, 6985, '("Subject", ["name"])'),
    # ⛔ The Object half of the same declaration had NO row, and that absence is ISSUE 1 of the
    # `#13489` review: §2.5's header cited `:6869`, which `:6984`/`:6985`/`:6990` had left 115/116/121
    # lines behind — and `:6989` is the `("Object", ["name"])` row that had no pin at all, so the
    # one sibling a reader would actually chase was the uncovered one. An absent pin is not
    # coverage; the header now names it beside its three siblings and this row resolves it.
    (_AUDIT, "`:6989`", _PI, 6989, '("Object", ["name"])'),
    (_AUDIT, "`:6990`", _PI, 6990, '("Source", ["_searchText"])'),
    (_AUDIT, "record_serve.py:468", _RS, 468, "READ_POINT_BY_ID: AnchoredRead("),
    (_AUDIT, "goal_judgement.py:1128", _GJ, 1128, "where = \"MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator\""),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "sdk.py:24626", "tortoise/sdk.py", 24626, "(n:Point {id:$id})-[:TAGGED]->()"),
    (_AUDIT, "search_engine.py:7184", _SE, 7184, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    # --- docs/architecture/SUBSTRATE-REBUILD-MAP.md §5's move list (the #11682 corpus-join block)
    (_SUBSTRATE, "tortoise/search_engine.py:3508", _SE, 3508, "``:Source``/``:Document`` row must not contribute"),
    (_SUBSTRATE, "`:3506`", _SE, 3506, "# #11682: the corpus join is PER FAMILY"),
    # ⛔ The two `:Document` addresses §5's #12915 move names had NO pin, and that is how BOTH shipped
    # one line high — `:37143`/`:37185` are the `:Source` lines of those two docstrings, so the cites
    # pointed a reader just OUTSIDE the set §5 censuses (§5 counts `git grep -n` lines carrying
    # `:Document`; `grep -nF 'D10: a document is a ``:Source``, not a'` returns the line ABOVE the
    # `:Document` one, which is how the +35-instead-of-+36 arithmetic was introduced and never caught).
    # Pinned by CONTENT on the line that carries `:Document`, so the next re-measure by that phrase reds.
    ("docs/architecture/SUBSTRATE-REBUILD-MAP.md", "tortoise/sdk.py:37547", "tortoise/sdk.py", 37547, "``:Document`` node)."),
    ("docs/architecture/SUBSTRATE-REBUILD-MAP.md", "`:37589` in `_file_human_approval`", "tortoise/sdk.py", 37589, "``:Document`` node)."),
    # --- docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md
    (_M3_BRIEF, "tortoise/search_engine.py:2945", _SE, 2945, "def run_fts_query"),
    (_M3_BRIEF, "search_engine.py:2787", _SE, 2787, "def recency_tie_clause"),
    (_M3_BRIEF, "tortoise/search_engine.py:5074", _SE, 5074, "for rank, (pid, _score) in enumerate(ranked)"),
    (_M3_BRIEF, "`search_engine.py:4993`", _SE, 4993, "def rrf_fusion("),
    # --- docs/architecture/ENTITY-VERDICT-BANDS.md — the D-1 row "the retrieval filter is status-only"
    # ⚠️ This row is a SECOND false correction, of the same class this file exists for. The document
    # cited `search_engine.py:2973-2997`; other PRs' inserts had already moved the status filter to
    # `:3020` on `origin/main`, so the cite was ~44 lines stale BEFORE this branch's `+3`. Adding the
    # `+3` (`2973-2997` -> `2976-3000`) therefore preserved the staleness while reading as a correction,
    # and `2976-3000` is the `build_or_query` byte-identity comment block, not the filter. The pin below
    # is anchored on the line that IS the filter, so the next offset-only edit reds instead of shipping.
    (_EVIDENCE, "search_engine.py:3138-3153", _SE, 3138, "if label in _STATUS_FILTERED_LABELS"),
    # --- docs/evidence/13083-capability-census/README.md
    (_CENSUS, "cache_projection.py:2776", _CP, 2776, "tail_denied = forbidden | {"),
    # ⚠️ Row 13b's Source-writer cite, added because the site had NO pin at all — the same shape
    # the `:6989` row above was added for. It sat at `:19637` (a comment about provenance loss in
    # the `sessionCaptured` stamp block) for merges without a red, because
    # `rg -n '19637' docs/ tests/ tools/ config/` reached this one row and no `_PINS` /
    # `_TOKEN_OCCURRENCES` entry covered it. ⛔ `#13486` moved the writer under this row, so it is
    # re-derived here to the line that actually carries `search_text = _source_search_text(`
    # (`grep -n 'search_text = _source_search_text(topics=topics)' tortoise/sdk.py`), not carried.
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "sdk.py:22225", "tortoise/sdk.py", 22225, "search_text = _source_search_text(topics=topics)"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "sdk.py:22267", "tortoise/sdk.py", 22267, "s.summary=CASE WHEN $sum"),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "`:22390`", "tortoise/sdk.py", 22390, "s.summary, s.topics, s."),
    (_AUDIT, 'hosted_api.py:23501', 'tortoise/hosted_api.py', 23501, 'MERGE (s:Source {url:$did})'),
    (_AUDIT, '`:23313`', 'tortoise/hosted_api.py', 23313, 'def _execute_commit_writes'),
    (_AUDIT, '`:34276`', 'tortoise/hosted_api.py', 34276, 'MERGE (p:PackInstall {namespace: $ns})'),
    # --- the four corrections this pass made that NO row reached (review cycle 3, F5). Each token
    # carries its own wording, not a bare number, because a bare `:NNNN` is satisfied by any sibling
    # spelling of the same digits — the defect `test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence`
    # exists for, and the reason row 13's parenthetical and §2.1's count marker survived three passes.
    (_AUDIT, '`:5434` at this head', 'tortoise/projection/entities.py', 5434, 'def _upsert_document(self, ev: dict) -> None:'),
    (_AUDIT, '`:7240` at this head', 'tortoise/projection/entities.py', 7240, 'def _upsert_source('),
    # ⚠️ Widened from the bare `` `:6681` `` this pass first wrote (review cycle 6, F5): a bare `:NNNN`
    # is exactly the shape the comment above says is excluded — it is satisfied by any sibling spelling
    # of the same digits — and this row was the one of the four that carried it.
    (_AUDIT, '`:7150` inside the same function', _SE, 7150, 'if expand_ops:'),
    (_AUDIT, '`:3062` derives the label', _SE, 3062, 'label = "Source" if entity_type =='),
    # --- three re-derived addresses that had NO pin at all, so a `+1` mutation left 441 tests green
    # (review cycle 6, P2-2). `grep -rn '28008\|28372' tests/ tools/` returned nothing before these rows:
    # the guard set is per-token, and an unlisted token is coverage the file only APPEARED to have.
    ("docs/00_index.md", "`sdk.py:28581` at this head", "tortoise/sdk.py", 28581, "WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL"),
    # ⛔ #14087 review round 7 (P3): the SAME `00_index.md:118` line writes a SECOND present-tense
    # this-head address — `the bullet is entity_verdict.py:43` — and no arm read it. Its sibling
    # (`sdk.py:28581`, the row above) was pinned, which is exactly how an unpinned neighbour hides:
    # `grep -c 'entity_verdict\.py:43' tests/test_12184_shifted_cites.py` -> 0 before this row, so a
    # re-point or a rotation of `entity_verdict.py` under it would have moved the address with every
    # arm green. One ROW per SITE, never one row per STRING.
    (_INDEX, "`entity_verdict.py:43`", _EV, 43, "writing `dedup_reviewed='link'` is worse than inert"),
    ("docs/architecture/ENTITY-VERDICT-BANDS.md", "(`sdk.py:28581`), so any non-NULL", "tortoise/sdk.py", 28581, "WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL"),
    ("docs/architecture/ENTITY-VERDICT-BANDS.md", "called at `sdk.py:28945`", "tortoise/sdk.py", 28945, "result = apply_entity_merge("),
    # --- docs/plans/2026-09-25-4997-vector-index-parity.md — the vector-index reader table
    # ⛔ The branch's `+147` to `hosted_backup.py` moved `def _index_has_is_operator()` from
    # `origin/main`'s `:2519` to `:2666` (`:2684` is that def's DOCSTRING line, so the `+4` that
    # aimed at `2684` was aiming at prose), and this branch re-pointed the row to `:1548` — a `+4`
    # applied to a number that was ALREADY wrong at `main` (`:1544` there, on a `restore_graph`
    # comment block). `:1548` opens on a comment whose text has nothing to do with the symbol, so the
    # row shipped naming a symbol at a line that does not carry it. Re-derived by CONTENT:
    # `grep -n 'def _index_has_is_operator' tortoise/hosted_backup.py` -> `2683` on this tree (it was
    # `2666` at the head that first re-derived this row; the pin below is what reddens if it moves
    # again). Pinned here because
    # an unlisted token is coverage this file only APPEARED to have (the `:6989` shape).
    (_PLAN_4997, "tortoise/hosted_backup.py:2683", _HB, 2683, "def _index_has_is_operator() -> bool:"),
    # --- #14043 review, finding 3: FOUR addresses this revision writes at THIS head were in neither
    # the header's enumeration nor any pin here, so the header's own reader rule resolved them to the
    # PIN — where a review measured that all four land on something else (a docstring, a comment, an
    # `if not rows` guard and a bare `)`). An unlisted token is coverage this file only APPEARED to
    # have; these rows are the same shape as the `:6989` and `hosted_backup.py:2683` rows above.
    # NON-VACUOUS: move any of the four by `+1` and the content arm reds (the token arm cannot fire —
    # the document still carries the address); measured at this head, each fragment below is the
    # line's own text, and at `ccb49741b2ec` none of the four numbers carries it.
    (_AUDIT, "live.py:667", "tortoise/live.py", 667, 'EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")'),
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "sdk.py:15873", "tortoise/sdk.py", 15873, "\"MATCH (m:GraphEventMeta) RETURN m.first_seq\""),
    (_AUDIT, "hosted_backup.py:844", _HB, 844, '"MATCH (m:GraphEventMeta) RETURN max(m.last_seq), max(m.first_seq)"'),
    (_AUDIT, "hosted_backup.py:916", _HB, 916, '"MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = $first"'),
    # §2.6's row, re-pointed to this head by the same review: the census's `:890`/`:1182`/`:1383` do not land
    # on these statements at this head, and `hosted_backup.py` drifts in TWO steps (`+2` above the pin's `:58`,
    # `+132` above the pin's `:1175`) — so the offsets are `+2`/`+134`, not one delta. Pinned by content
    # because the header's enumeration names them and promises exactly that.
    (_AUDIT, "hosted_backup.py:892", _HB, 892, '"MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)"'),
    (_AUDIT, "hosted_backup.py:1333", _HB, 1333, 'MATCH (n) RETURN labels(n), properties(n)'),
    (_AUDIT, "hosted_backup.py:1534", _HB, 1534, 'MATCH (n) RETURN labels(n), properties(n)'),
    # --- #14043 review, finding 2 (the FINAL-head review): five addresses this revision writes in its
    # new §2.2 `:Source` reader row and in row 13 had NO pin and no occurrence count, so the next merge
    # that inserted into `tortoise/session_source_prose_migration.py` would have moved all five with
    # nothing red. ⛔ These are NOT head declarations: that file is the SAME BLOB at the pin
    # `ccb49741b2ec` and at this head (`git rev-parse ccb49741b2ec:tortoise/session_source_prose_migration.py`
    # == `HEAD:` → `77c86b24e5`), so all five open at the pin exactly as they open here and the header's
    # pin default resolves them CORRECTLY. What was missing is the guard, not the head — the live defect
    # here is an unguarded cite, which is the same coverage this file only APPEARED to have that the
    # `:6989` and `hosted_backup.py:2683` rows above were added for (the document's §2.7 note:
    # file-scoped drift is "either fixed everywhere or trusted nowhere").
    # NON-VACUOUS: move any of the five by `+1` and the content arm reds (the token arm cannot fire —
    # the document still carries the address); each fragment below is the line's own text at this head,
    # taken with `grep -n 'def …'/the SQL literal` on this tree, never by a delta. ⚠️ `:218-223` is
    # pinned on the ONE-TOKEN spelling, whose two document sites are the same address (the §2.2 row's
    # `tortoise/…` spelling and row 13's bare one) — a single content pin resolves both, and the
    # occurrence count is what stops a third site appearing unannounced.
    (_AUDIT, 'session_source_prose_migration.py:218-223', _SSM, 218, '_SOURCE_ROWS = ('),
    (_AUDIT, '`:1263-1268`', _SSM, 1263, '_HITS_ROWS = ('),
    (_AUDIT, '`:274-281`', _SSM, 274, '_REPAIR_PAIR = ('),
    (_AUDIT, 'session_source_prose_migration.py:277-279', _SSM, 277, "AND coalesce(s.summary,'')=$old_sum "),
    (_AUDIT, '`:1292-1300`', _SSM, 1292, '_CONTROL_LEXICON_ROWS = ('),
    # --- #14043 review (finding 1, this cycle): §6.3's own two addresses were in neither list and
    # unpinned, and the header's default resolves them to the PIN — where `sdk.py:16077` is unrelated
    # prose and `sdk.py:3801` is an EMPTY LINE. Both are one-token spellings already, so no figure
    # moves (345 as-written stays 345); NON-VACUOUS: `+1` reds the content arm at both.
    # ⛔ #14087 review: the span's END was one short — `:16081` is the paragraph's PENULTIMATE line and
    # `:16082` (the line reading "turns list is that same requirement for the record-held lane.") is its
    # last, with `:16083` blank. A span token pins only its START, so the short end was invisible to `_PINS`;
    # `test_the_jsonl_contract_span_ends_on_its_paragraphs_last_line` derives the end from the source
    # and grades it.
    ("docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md", "sdk.py:16077-16082", "tortoise/sdk.py", 16077, "**JSONL event log (#548):** written when *point* is provided"),
    (_AUDIT, 'sdk.py:3801', _SDK, 3801, 'sdk._emit_event("PointRetracted", _deleted_point_payload(tid))'),
)

#: The `main`-set group the `#13124` review found OUTSIDE the header's `main` enumeration: (token the
#: header must carry, target file, 1-based line **on this branch's tree**, verbatim fragment on that
#: line). Five of these
#: are reached by `_PINS` already; `:2485`, `:2542` and `projection/entities.py:5797` were in NO pin at all
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
    (":5824", _ENT, 5824, "s.summary=coalesce($summary, s.summary, '')"),
    # Round 8: written at `main`, pinned at `main`, but absent from the enumeration — so the header's rule
    # sent readers to the pin. ⛔ NOT "where every one is a comment": at the pin `sdk.py:24059` is a DELETE
    # statement and `hosted_api.py:22919` is `if is_subject_kind(...)`, both executable. The pin is wrong for
    # all six; it is a comment for most, and unrelated CODE for the two that matter most here.
    ("sdk.py:24626", "tortoise/sdk.py", 24626, "(n:Point {id:$id})-[:TAGGED]->()"),
    ("search_engine.py:7184", _SE, 7184, "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point)"),
    ("sdk.py:22267", "tortoise/sdk.py", 22267, "s.summary=CASE WHEN $sum"),
    (":22390", "tortoise/sdk.py", 22390, "s.summary, s.topics, s."),
    ("hosted_api.py:23501", "tortoise/hosted_api.py", 23501, "MERGE (s:Source {url:$did})"),
    (":23313", "tortoise/hosted_api.py", 23313, "def _execute_commit_writes"),
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
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4950-4954'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7153'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7560'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7562'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7559-7562`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7565-7578`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3157'): 5,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '| `tortoise/search_engine.py:4878-4880` |'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4293'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:3062'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3129`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2712-2723'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2724-2726'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2714-2715`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:2776'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2921`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:37883`'): 1,
    # 3, not 2: the whole-node walk's address is written BARE at three sites — the header's pair
    # (`sdk.py:37883`/`:37906`), the proximity paragraph's "they are `:37883` and `:37906` now", and
    # §2.5's distance paragraph ("23 lines below at `:37906`", re-derived from the stale `:37751`).
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:37906`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '| `tortoise/sdk.py:37906` |'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'the `-[r]->` walk (`sdk.py:37906`)'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'they are `:37883` and'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:58099` passes `"event"`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`audit.py:817`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2452`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:2463`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`mcp_server.py:5250`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`mcp_server.py:11590`'): 1,
    # 4: the header's BARE spelling of the same address, the §2.5 five-address enumeration's bare
    # site, and the two sites the #14246 fix lane added when it re-measured the comparability split
    # (`mcp_server.py`'s `:11590` is the ELEVENTH non-comparable pair, so the split must name it, and
    # §2.5's head-declaration sentence states where it sits at the pin). The spelling that was counted
    # here before `#14169` moved the address (`:11281`, on the `#13991` provenance sentence) is NOT
    # counted: that sentence is the record of the correction, not a cite of this head. Measured on this
    # document: `grep -o '`:11590`' docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | wc -l` -> 4.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:11590`'): 4,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5720`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:8515`'): 2,
    # The four bare `sdk.py` `:Tag` siblings the header paragraph enumerates, one occurrence each —
    # the §2.5 spellings are the one-token keys `sdk.py:31551` etc. and are a different key.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:31551`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:31533`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:24513`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:24857`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6974-6990`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6984`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6985`'): 1,
    # 2: `:6989` is BOTH the header region's disclosure of THIS tree's Object declaration (it sits
    # before `## 1.`) and the §2.5 label-scan table row's own key. Two sites, one spelling — a single
    # pin resolves the line, and the count is what stops a third occurrence appearing unannounced.
    # BOTH spellings arrive with this branch's re-derivation (`f9e1bcc66a`), not with `#13489`:
    # `origin/main` carries ZERO of them, and this head carries two. Measured at THIS head:
    # `grep -o '\`:6989\`' docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | wc -l` -> 2.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6989`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:6990`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'record_serve.py:468'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'goal_judgement.py:1128'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:24626'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7184'): 2,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', 'tortoise/search_engine.py:3508'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:3506`'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', 'tortoise/sdk.py:37547'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:37589` in `_file_human_approval`'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:2945'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'search_engine.py:2787'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', 'tortoise/search_engine.py:5074'): 1,
    ('docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md', '`search_engine.py:4993`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'search_engine.py:3138-3153'): 1,
    # ⛔ `origin/main`'s two `sdk.py:28096` rows are DROPPED at this merge, not carried: `28019` is
    # `main`'s value for the content-review filter, and this branch re-derived that cite to `:28067`
    # in BOTH documents. Measured at this head: `grep -rn 'sdk.py:28096' docs/00_index.md
    # docs/architecture/ENTITY-VERDICT-BANDS.md` -> no match (the token survives only in
    # `05-verify.md`'s sentence stating the re-point), so a row asserting 1 occurrence would be a
    # false pin over a token the documents no longer carry.
    # ⚠️ THAT WAS HOP ONE, and these two rows below are HOP TWO (`#13746`): `28067` itself rotted, and
    # the same re-point moved both documents to `28178` — which is also where `#13724`'s own
    # `tortoise/sdk.py` insertion independently landed it (`_PINS` above follows it). So `28019` and
    # `28067` are BOTH tokens the documents no longer carry, and a row for either is a false pin.
    ('docs/evidence/13083-capability-census/README.md', 'cache_projection.py:2776'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:22225'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:22267'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:22390`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:23501'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:23313`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:34276`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5434` at this head'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7240` at this head'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7150` inside the same function'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3062` derives the label'): 1,
    ('docs/00_index.md', '`sdk.py:28581` at this head'): 1,
    # ⛔ #14087 review round 7: the pin row added for the SAME line's other present-tense address.
    # Occurs ONCE in the document (`grep -o 'entity_verdict\.py:43' docs/00_index.md | wc -l` -> 1), so a
    # second site must not appear unannounced.
    ('docs/00_index.md', '`entity_verdict.py:43`'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', '(`sdk.py:28581`), so any non-NULL'): 1,
    ('docs/architecture/ENTITY-VERDICT-BANDS.md', 'called at `sdk.py:28945`'): 1,
    ('docs/plans/2026-09-25-4997-vector-index-parity.md', 'tortoise/hosted_backup.py:2683'): 1,
    # #14043 review, finding 3: the four addresses this revision writes at THIS head. ONE site each
    # except `live.py:667`, which the document names three times (§2.1's table, the dead-pair paragraph
    # and row 2's summary) — all three are the same address, so a re-point at any one of them is what
    # the count is here to red. Measured: `grep -o 'live.py:667' <the document> | wc -l` -> 3.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'live.py:667'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:15873'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_backup.py:916'): 1,
    # §2.6's re-pointed row: `:892` is written twice (the row, and the header's enumeration that promises it is
    # pinned), the other two once each. The count is what reds a re-point at any of them.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_backup.py:892'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_backup.py:1333'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_backup.py:1534'): 1,
    # #14043 review (P2, `hosted_backup.py` file-scoped drift): `hosted_backup.py:844` is now TWO sites,
    # and both are deliberate. §2.7's `:GraphEventMeta` row carries the statement; §2.6's rule sentence
    # must NAME the same address, because its claim is "all three of which §2.7 cites" — a cross-reference
    # that could not be resolved by grep while it named the census's `:842`. Measured:
    # `grep -o 'hosted_backup.py:844' <the document> | wc -l` -> 2.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_backup.py:844'): 2,
    # #14043 review, finding 2: five UNPINNED addresses in this revision's own new §2.2 `:Source`
    # reader row and in row 13 (pin-stable — the file is one blob at the pin and at this head — so these
    # counts guard a RE-POINT, not a head), named once in the header and pinned above. Measured on
    # this document at this head (`grep -o -F '<token>' <the document> | wc -l`): the one-token
    # `:218-223` spelling is carried 2x (the §2.2 row's `tortoise/…` form contains it, plus row 13's
    # bare form — one address, two sites), and each bare sibling is carried 2x (its row plus the
    # header sentence that now enumerates it) except `:1263-1268`, which row 13, the §2.2 row and the
    # header all carry — 3x.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'session_source_prose_migration.py:218-223'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:1263-1268`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:274-281`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'session_source_prose_migration.py:277-279'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:1292-1300`'): 2,
    # #14043 review (finding 1): one site each in §6.3. Measured on this document:
    # `grep -o -F 'sdk.py:16077-16082' <doc> | wc -l` -> 1, `... 'sdk.py:3801' ...` -> 1.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:16077-16082'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:3801'): 1,
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


#: ⛔ RESTORED (#13494, fresh-context review at `1038810281`). This arm exists on `origin/main` and was
#: DROPPED by the `#13345` integration merge (`66e5fe9293`) without a word in any commit message; every
#: later forward merge re-dropped it because the branch tip already lacked it. It is RED on `main` (the
#: D-1 promise is broken there: `docs/00_index.md` says `sdk.py:28096`, `ENTITY-VERDICT-BANDS.md` says
#: `sdk.py:28096` and that line is `"IMPL", pid, [prior], label="alreadyDecided",`) and GREEN on this
#: PR's tree — so deleting it bought nothing and cost the only arm that DERIVES the address instead of
#: comparing two hardcoded literals. `_PINS` cannot replace it: a coordinate edit of the doc token, the
#: pin line and the pin fragment satisfies every pin while two documents still name two different lines.
#: Its docstring said exactly that ("The CROSS-DOCUMENT arm — `_PINS` cannot see it"), and nothing at
#: HEAD re-implemented it: `grep -n 'address_beside' tests/test_12184_shifted_cites.py` returned no
#: match at `1038810281` — it returns matches NOW, because this is the restore that put them back.

def test_the_dedup_filter_is_one_address_in_both_documents_that_promise_it() -> None:
    """The CROSS-DOCUMENT arm — `_PINS` cannot see it.

    `docs/00_index.md`'s D-1 row states the promise in as many words: *"the filter it names is at
    `sdk.py:<n>` at this head, and `ENTITY-VERDICT-BANDS.md` carries the same number"*. Every row in
    `_PINS` is satisfied by its OWN document, so two documents naming two DIFFERENT lines both pass every
    pin — which is the shipped `#13345` state, measured: `docs/00_index.md` `sdk.py:27943` (prose at the
    head, and the filter at no head) against `ENTITY-VERDICT-BANDS.md` `sdk.py:27938` (the value before
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
    instruction, and `test_the_published_denominator_is_the_count_of_this_file` below is the half of that
    which CAN be checked without history; the NUMERATOR (the drifter count) cannot be, because it compares
    two historical blobs: `git show <head>:<path> | sed -n '<N>p'` at each of them, text for text — that
    rule is what this revert guard's literals are a change-detector for, not a re-measurement of.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    for claimed in (
        "138 of this document's 234",
        "dedupes the **345** as-written spellings to 234 — **111**",
        "345 written spellings → 234 pairs → 111 collisions → 138 drifters of the 234",
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
        # asserted rather than measured: the basis-INVARIANCE is false (counting the two-code-span pairs
        # adds four addresses, THREE of them drifters (`cache_projection.py:2485` and `sdk.py:2917`;
        # `sdk.py:61649` is past both heads' EOF, so it is non-comparable and adds nothing), so the
        # numerator moves 127 -> 130 while the denominator moves 223 -> 227), and "±5"
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


#: An as-written one-token `path.py:NNNN` spelling — the header's stated population. A RANGE cite is one
#: spelling and counts once, at its START line (`(\d+)` stops there); the two-code-span form the header
#: describes is deliberately NOT matched, exactly as the header says it is resolved but not counted.
_SPELLING = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_./-]*\.py):(\d+)")
#: The TWO-code-span form the header also writes (`` `path.py` `:NNNN` ``). `_SPELLING` cannot see it — the
#: `:NNNN` is not adjacent to the `.py` — so the one-token basis RESOLVES but does not COUNT these, which is
#: exactly what the header's alternative-basis sentence discloses. Counting them here, from the document,
#: is what makes that disclosure a measurement: it shipped as the asserted constant "one pair" while the
#: document wrote MORE THAN ONE, so the comparator enforced a figure the tree contradicts (`#13854`).
_TWO_SPAN = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.py)`\s*`(?::(\d+))")
#: ⛔ The figures are matched with the header's EMPHASIS STRIPPED, never against one rendering of them.
#: `main` bolds the spelling and collision counts (`dedupes the **327** … — **113**`) and not the ratio's
#: three mentions, and which numbers it emphasises is an EDITORIAL choice that has already moved twice —
#: the patterns before this one were written against an un-bolded edition, found nothing on the merged tree,
#: and turned the comparator into an early `return ["the header no longer publishes …"]` that read like a
#: finding on CORRECT text (`#13599` review, finding 1). Tolerating the markers number-by-number fixes the
#: instance; stripping them fixes the class — the ratio patterns below carried the SAME coupling and would
#: have false-redded the day the sentence dropped its bold. Emphasis is presentation; the FIGURES are the
#: claim. (The exact shipped rendering is still pinned, by the revert guard
#: `test_the_measured_figures_and_the_pin_side_descriptions_are_the_re_measured_ones` — which is where a
#: rendering belongs. This is a comparator, and it must not confuse the two.)
def _unbolded(text: str) -> str:
    """*text* with markdown bold markers removed, so a published figure matches whatever its emphasis."""
    return text.replace("**", "")


_TRIPLE = re.compile(r"dedupes the (\d+) as-written spellings to (\d+) — (\d+)")
_QUADRUPLE = re.compile(
    r"(\d+) written spellings → (\d+) pairs → (\d+) collisions → (\d+) drifters of the (\d+)(?: comparable)?"
)
_ALT_BASIS = re.compile(r"counting that form gives (\d+) pairs and (\d+) drifters")
#: The two-code-span disclosure's OWN counts — "writes N pairs as two code spans", and "M of them are
#: resolved but not counted by the one-token rule". Both were PROSE ONLY until the `#13991` review (`ab6f915b8d`, finding
#: P2-A) measured them: the sentence stated **three** pairs while the document writes **five**, so the one
#: numeral that sanctions the alternative basis was the one figure in it nothing re-derived. A reader who
#: took the reconciliation at its word could not reach the `227` published beside it — `223 + 3 != 227` — and
#: the added-pair count was left to be inferred rather than read. The published `227`/`130` were measured by
#: `_ALT_BASIS` the whole time; the sentence JUSTIFYING them was not, which is the `#13599` class one level
#: up (a figure quoted from a document rots while the comparator beside it stays green). The prose was
#: corrected by the lane that landed `#13991`; nothing GRADED it, so this arm is the durable half: without
#: it the next re-measurement of this document can state any number here and the comparator stays green.
#: The spelled numerals these two sentences may state a count in, and the ONLY source both the patterns
#: and `_as_int` read — so the class the patterns ACCEPT cannot drift from the class the grader GRADES.
#: ⛔ The ceiling is STATED here, not accidental. It used to stop at `ten` while `_NUMERALS` already
#: graded `zero`, and the mismatch produced exactly the verdict this file's header forbids: an edition
#: legitimately stating `zero pairs` (the form is cleaned up, nothing left to disclose) or `eleven pairs`
#: got "the header no longer states how many…" about a sentence that is right there — the false-absence
#: class of `#13599` finding 1, reported two review rounds running (`#14010` rounds 3 and 4). A prefix
#: match (`four` inside `fourteen`) could never report a wrong FIGURE — the literal after the group cannot
#: be completed by a prefix — so the only outcome above the ceiling was that false absence verdict.
#: `zero`/`none` are entries, not decoration: an honest re-measurement can find NO two-code-span pair
#: left, and `zero` shipped as a `_NUMERALS` key no pattern could ever feed (dead entry, `#14010` round 4).
#: ⛔ The ZERO spellings are named once here, and both structures below are derived from this tuple — the
#: table that used to write them out a second time and number the rest with `_SPELLED_NUMERALS[2:]` was
#: coupled BY INDEX: inserting one spelling re-numbered every spelled count after it, so a correct
#: `one of them …` was graded `2` and red on correct text. Extending the vocabulary is the edit the comment
#: invited, and it silently broke the grader (`#14010` round-5 review, P2).
_ZERO_NUMERALS: tuple[str, ...] = ("zero", "none")
_SPELLED_NUMERALS: tuple[str, ...] = (
    *_ZERO_NUMERALS,
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
)
#: Spelled numerals are graded as NUMBERS, not treated as an absent statement. The edition this guard was
#: written against states `three pairs` IN WORDS, so a digits-only pattern reported "the header no longer
#: states how many pairs…" — a MISSING-SENTENCE verdict about a sentence that is right there, one digit
#: wrong, which is the false-negative shape `#13599` finding 1 punished on the other side of this file.
#: ⛔ DERIVED from `_SPELLED_NUMERALS`, never written out again: a third hand-kept copy of the vocabulary is
#: how the pattern's ceiling and the grader's class came apart in the first place. The positive spellings are
#: sliced off `_ZERO_NUMERALS`' own length, so adding a zero spelling moves the boundary with it.
_NUMERALS: dict[str, int] = {
    **dict.fromkeys(_ZERO_NUMERALS, 0),
    **{
        word: value
        for value, word in enumerate(_SPELLED_NUMERALS[len(_ZERO_NUMERALS) :], start=1)
    },
}
#: The examples the two not-found messages render (`zero/none/one`), DERIVED for the same reason: a literal
#: `_SPELLED_NUMERALS[:3]` mislabels the accepted class the moment a spelling is inserted before `one`.
_CLASS_EXAMPLES = "/".join((*_ZERO_NUMERALS, _SPELLED_NUMERALS[len(_ZERO_NUMERALS)]))
#: Longest first, so `sixteen` is tried before `six`. An unanchored alternation over spelled words
#: normally needs `\b` to stop `four` matching inside `fourteen`; here the required literal after the group
#: (` pairs? as two code spans`, ` of them (?:is|are) resolved`) already makes a trailing prefix match
#: unable to complete, so the ordering is defence and the literal is the guarantee on that side.
#: ⛔ `\b` on the LEADING side is not defence — it is the guarantee there, and there was none. The literal
#: after the group binds the right edge only, so `the recount rewrites five pairs as two code spans`
#: matched `writes five pairs…` and `everyone of them are resolved…` matched `one of them…` — both
#: LOWERCASE, because both patterns are case-SENSITIVE (an uppercase `REWRITES` matched nothing even before
#: the fix, so quoting it as the evidence made this paragraph unverifiable: `#14010` round-7 review). Both
#: arms grade the
#: leftmost match, so an edition whose prose contains an earlier `…rewrites five pairs…` while its real
#: disclosure says six would be graded on the wrong sentence, red on correct content — and a DISCLOSURE that
#: is genuinely missing could be masked by such a sentence (`#14010` round-6 review, P2). Measured with the
#: boundaries: both false matches are gone, and the shipped document (`also writes five pairs…`,
#: `four of them…`), `none of them …` and the digit forms still match.
_NUMERAL_ALT = "|".join(sorted(_NUMERALS, key=len, reverse=True))
_TWO_SPAN_TOTAL = re.compile(rf"\bwrites (\d+|\b(?:{_NUMERAL_ALT})) pairs? as two code spans")
#: ⛔ `(?:is|are)`, for the same reason `_TWO_SPAN_TOTAL` writes `pairs?`: number AGREEMENT is a property of
#: the count, not of the claim. `one of them IS resolved` is how a lane re-measuring to a single uncounted
#: pair writes that sentence — the singular form for the one count that takes it — and a plural-only pattern
#: could not read it. That was never the false-ABSENCE verdict the messages above were reworded to avoid (it
#: reported the count as one `in a form this guard reads`), which is worse in one way: the document is
#: RIGHT, the guard says it cannot read it, and the lane re-measuring is sent to reword a correct sentence
#: instead of to its figure (`#14010` re-review, P2). `zero`/`none`/`two`.. keep `are`.
_TWO_SPAN_NEW = re.compile(
    rf"(\d+|\b(?:{_NUMERAL_ALT})) of them (?:is|are) resolved but not counted by the one-token rule"
)


def _as_int(token: str) -> int:
    """*token* as an int, spelling the numerals this header may write as words (`three`) as well as digits.

    The spelled class is `_NUMERALS` — `zero`/`none` through `twenty` — and it is the SAME mapping the
    patterns' alternation is built from, so a token the pattern captures is always a token this grades.
    """
    return int(token) if token.isdigit() else _NUMERALS[token]


def _two_span_total_mutant(stated: int) -> int:
    r"""A two-code-span pair count that CONTRADICTS *stated* and that `_TWO_SPAN_TOTAL` can still read.

    ⛔ The control at `test_a_carried_forward_figure_is_reported` asserts only that the mutated document
    reports SOME problem, so a mutant whose value the count pattern cannot parse proves nothing: the red
    then comes from the ABSENT-SENTENCE arm, and the count comparison the control exists for was never
    consulted (a false PASS of the control — `#14010` round-4 finding 1, reported as a `-1 pairs` value
    that `\d+` will not read because the character after `writes ` is `-`).

    The P2-A narrative is the BELOW direction (`three` stated where five are written), so that is kept
    wherever it parses and decreases — `stated - 2` for every measurement of 2 or more. Below 2 it does
    not exist (an honest edition measuring one pair gives `-1`, measuring none gives `-2`), so the mutant
    takes the OVERSTATE direction instead: `+1` parses at every measurement including 0 and always
    disagrees. There is no floor that repairs the below direction at 0 (`max(stated - 2, 0) == stated`
    there), which is why the fallback is a direction, not a clamp.
    """
    below = stated - 2
    return below if below >= 0 else stated + 1


def _mutate_numeral(match: re.Match[str], value: int) -> str:
    r"""The matched sentence with its OWN numeral rewritten to *value*, spelled or in digits.

    `_mutate` splits on `\d+` and so cannot see a numeral the header SPELLS: both counts in the
    two-code-span sentence are published as words (`writes five pairs`, `four of them`), which is
    precisely the edition that shipped the wrong one. The positional guarantee `_mutate` exists for is
    kept POSITIONALLY: `str.replace(…, 1)` rewrites the LEFTMOST occurrence, so the edit lands on
    `group(1)` exactly when `group(1)` is where that leftmost occurrence sits — which is asserted here.

    ⛔ Counting occurrences (`match.group(0).count(token) == 1`) proves something else, and it is FALSE BY
    CONSTRUCTION for numerals this file's own patterns accept, on CORRECT text: the sentence scaffold
    spells `two` (`… writes two pairs as TWO code spans`) and `one` (`… the ONE-token rule`), so a
    document legitimately measuring two two-code-span pairs, or ONE uncounted pair, tripped it. Both are
    real measurements — the patterns accept `_SPELLED_NUMERALS` and `_as_int` grades all of them — so the
    count could only ever fire on correct content, at the next lane re-measuring this header, and pointed
    them at this helper's token bookkeeping instead of at their figure (`#13599` review, finding 1: a
    control that reds on CORRECT text). The offset comparison carries the same guarantee and is true
    wherever the guarantee holds.

    ⛔ "Asserted here" is worth nothing unless a red is REACHABLE: for both patterns that call this, the
    capture sits at the numeral's own offset on every input they accept, so no document can violate it and
    deleting this `assert` left the suite green (`#14010` round 3, finding 2 — dead weight under #3442
    rule 3). `test_the_numeral_mutators_offset_assert_can_fire` supplies the synthetic match that DOES
    violate it, so the guard has a red of its own rather than a claim in a docstring.
    """
    token = match.group(1)
    offset = match.start(1) - match.start(0)
    leftmost = match.group(0).index(token)
    assert leftmost == offset, (
        f"`{token}` first occurs at {leftmost} in {match.group(0)!r}, not at the captured numeral's "
        f"offset {offset} — the leftmost rewrite would edit a different word than the one the pattern grades"
    )
    return match.group(0).replace(token, str(value), 1)


def _resolved_pairs(text: str, repo: Path = REPO) -> tuple[int, set[tuple[str, int]]]:
    """(as-written spellings, unique `(resolved path, start line)` pairs) for a document.

    The header's own resolution rule, as code: a written path that exists repo-relative (`tests/…`, `tools/…`,
    `tortoise/…`) is used AS WRITTEN and never prefixed; a directory-qualified path is the same rule with
    `tortoise/` restored (`projection/__init__.py`); a bare `name.py` is `tortoise/name.py` when that file
    exists, and the one file of that name otherwise (`entities.py` is `tortoise/projection/entities.py`).
    """
    spellings = _SPELLING.findall(text)
    pairs: set[tuple[str, int]] = set()
    for written, line in spellings:
        pairs.add((_resolve_path(written, repo), int(line)))
    return len(spellings), pairs


def _resolve_path(written: str, repo: Path = REPO) -> str:
    """The header's resolution rule for ONE written path (docstring in `_resolved_pairs`)."""
    if (repo / written).is_file():
        return written
    if (repo / "tortoise" / written).is_file():
        return f"tortoise/{written}"
    hits = sorted(
        p.relative_to(repo).as_posix() for p in (repo / "tortoise").rglob(written)
    )
    assert len(hits) == 1, (
        f"`{written}` resolves to {len(hits)} files under `tortoise/` ({hits}) — the header's "
        "rule assumes exactly one, so the pair it forms with is not the one the header counts."
    )
    return hits[0]


def _two_span_pairs(text: str, repo: Path = REPO) -> set[tuple[str, int]]:
    """The `(resolved path, start line)` pairs this document writes as TWO code spans.

    MEASURED, not asserted: the alternative-basis sentence discloses how many pairs this form adds, and
    nothing else in the file can tell whether that number is the tree's. It is not a second basis — the
    two sets are unioned with the one-token pairs to get the count the header publishes.
    """
    return {(_resolve_path(written, repo), int(line)) for written, line in _TWO_SPAN.findall(text)}


def _figure_problems(text: str, repo: Path = REPO) -> list[str]:
    """Every way the header's published drift figures disagree with a recount of the file they describe.

    Only the DENOMINATOR half is recomputed here (spellings, pairs, collisions, and the "N of those M"
    cross-references to them). The drifter numerator compares two historical blobs and needs history:
    `git show <head>:<path> | sed -n '<N>p'` at the pin and at the named `main`. So the numerator is checked
    only for INTERNAL agreement with the denominator and across its three published mentions.
    """
    problems: list[str] = []
    spellings, pairs = _resolved_pairs(text, repo)
    collisions = spellings - len(pairs)
    # Matched on the unbolded body: see `_unbolded` — a bold marker between a word and its figure is not a
    # disagreement about the figure.
    body = _unbolded(text)
    triple = _TRIPLE.search(body)
    quadruple = _QUADRUPLE.search(body)
    if not triple:
        return ["the header no longer publishes `dedupes the N as-written spellings to P — C` (matched with "
                "emphasis stripped)"]
    if not quadruple:
        return ["the header no longer publishes the `N written spellings → P pairs → C collisions → D drifters` "
                "sentence (matched with emphasis stripped)"]
    # ⛔ The two-code-span form is MEASURED from this document, never asserted as an offset: `_SPELLING`
    # cannot see it, so the count it contributes moves whenever the document does. Computed once here
    # because TWO arms grade it — the disclosure's own stated counts below, and the alternative basis it
    # justifies (whose `227`/`130` are these same pairs). ⛔ Placed AFTER the two early returns above:
    # `_two_span_pairs` resolves each written path through `_resolve_path`, whose ambiguity check is an
    # `AssertionError`. Hoisted above them, a header whose `_TRIPLE`/`_QUADRUPLE` sentence had been reworded
    # — a case this function grades as a plain problem STRING — instead died on an assertion from a
    # resolution the caller never needed, so the graded message became a crash (`#14010` final review,
    # finding 3).
    # ⛔ A resolution failure here is GRADED, never raised. `_resolve_path`'s ambiguity check is an
    # `AssertionError`, and it fires on exactly the documents this function exists to REPORT — a header
    # re-measured against a tree where a cite moved. Raising turns a graded verdict into a traceback AND
    # takes every other arm's finding with it, which is the same class as the two missing-sentence states
    # fixed just above, one state further on (`#14010` round-6 review, finding 2: the measurement landed
    # below the `_TRIPLE`/`_QUADRUPLE` returns but still above `if not alt`).
    try:
        two_span: set[tuple[str, int]] | None = _two_span_pairs(text, repo)
    except AssertionError as exc:
        problems.append(
            "the two-code-span form cannot be resolved against this tree, so the counts that depend on it "
            f"are NOT graded: {exc}"
        )
        two_span = None
    # ⛔ `None`, not `set()`: the alternative basis is `published_pairs + extra_pairs`, so a zero fallback
    # would report a FIGURE this guard never measured — the false-figure class `#13599` finding 1 punished,
    # in the opposite direction. Every arm that reads it below is skipped while it is unknown.
    extra_pairs = None if two_span is None else len(pairs | two_span) - len(pairs)
    if tuple(map(int, triple.groups())) != (spellings, len(pairs), collisions):
        problems.append(
            f"the header publishes spellings/pairs/collisions {triple.groups()}, but this file MEASURES "
            f"{spellings}/{len(pairs)}/{collisions} by the header's own rule"
        )
    published_spellings, published_pairs, published_collisions, drifters, comparable = map(
        int, quadruple.groups()
    )
    if published_spellings != spellings:
        problems.append(
            f"the figures sentence counts {published_spellings} spellings; this file writes {spellings}"
        )
    if published_pairs != len(pairs):
        problems.append(
            f"the figures sentence counts {published_pairs} pairs; this file MEASURES {len(pairs)}"
        )
    if published_collisions != published_spellings - published_pairs:
        problems.append(
            f"{published_collisions} collisions is not {published_spellings} − {published_pairs}: the "
            "collision count is arithmetic on the pair count, so it moves WITH it (the `#13550` repair's "
            "inverse: `112` was carried forward over a pair count that had already moved to 216)"
        )
    if comparable != published_pairs:
        problems.append(
            f"the sentence says `of the {comparable}` while the denominator is "
            f"{published_pairs} — the ratio has to name its own denominator"
        )
    if not 0 < drifters <= published_pairs:
        problems.append(f"{drifters} drifters is not a numerator over {published_pairs} pairs")
    for pattern, what in (
        (re.compile(r"(\d+) of this document's (\d+)"), "the `N of this document's M` header line"),
        (re.compile(r"The drift is (\d+) of those (\d+)\."), "the `The drift is N of those M` restatement"),
    ):
        found = pattern.search(body)
        if not found:
            problems.append(f"{what} is gone — the ratio is published in three places on purpose")
        elif tuple(map(int, found.groups())) != (drifters, published_pairs):
            problems.append(
                f"{what} reads {found.groups()} while the figures sentence reads "
                f"{(drifters, published_pairs)} — they are one measurement"
            )
    alt = _ALT_BASIS.search(body)
    if not alt:
        problems.append("the header no longer states the alternative counting basis")
    else:
        alt_pairs, alt_drifters = map(int, alt.groups())
        # ⛔ MEASURED from the document, never asserted as an offset. The two-code-span form is invisible to
        # `_SPELLING`, so the count it adds is a property of THIS document and moves when the document does.
        # A hard-coded `+1` here enforced "one pair" while the document wrote four — the comparator became
        # the thing that was wrong, and it red on correct text the moment the document was measured.
        if extra_pairs is not None and alt_pairs != published_pairs + extra_pairs:
            problems.append(
                f"the alternative basis reads {alt_pairs} pairs, but this document writes {extra_pairs} "
                f"pair(s) in the two-code-span form the one-token basis resolves but does not count, so "
                f"over {published_pairs} it is {published_pairs + extra_pairs}"
            )
        # ⛔ A BAND, not a lower bound. The two bases are one measurement under two counting decisions
        # (`122 and 123 are one measurement under two counting decisions, not two findings`), and each added
        # pair adds AT MOST one drifter — so the only legal numerators are `drifters` .. `drifters +
        # extra_pairs`. `alt_drifters < drifters` saw the downward violation and was blind to every
        # overstatement: a numerator inflated by +8 or +67 over the primary's read `problems == []`, which
        # is the exact direction a stale figure takes when a denominator moves under it (`#13599` review,
        # finding 1 — measured on this head before the fix). It is the ONLY guard on this figure: the
        # three-mention agreement arm cannot reach the alternative basis, because the document publishes
        # `123` in that sentence alone, so there is no second mention to agree with.
        if extra_pairs is not None and not drifters <= alt_drifters <= drifters + extra_pairs:
            problems.append(
                f"the alternative basis reads {alt_drifters} drifters against the primary basis's "
                f"{drifters} — its {extra_pairs} added pair(s) can add at most {extra_pairs} drifter(s) and "
                f"cannot remove one, so it is {drifters}..{drifters + extra_pairs}"
            )
    # ⛔ The sentence that REACHES the alternative basis is graded here too. `#13991`'s review
    # (`ab6f915b8d`, P2-A) measured it publishing `three` pairs where the document writes five, and
    # `223 + 3 != 227`: the figure it sanctions was measured, the figure that sanctions it was not, so
    # a reader reconciling the two bases by arithmetic on the STATED three concluded the document was
    # inconsistent — and was right. Both counts are now measured from the same `two_span` set the
    # alternative basis is computed from, so they cannot disagree with it or with each other.
    total = _TWO_SPAN_TOTAL.search(body)
    if not total:
        # ⛔ Names the ACCEPTED CLASS and admits it cannot tell "reworded" from "dropped". The message it
        # replaces asserted the sentence was GONE, which is FALSE whenever the sentence is present in a form
        # the pattern does not read — the false-absence verdict `#13599` finding 1 punished, and the reason
        # the vocabulary above is now derived rather than hand-copied (`#14010` rounds 3 and 4). A lane that
        # re-measures this header must not be told to "restore" a sentence it is looking at.
        problems.append(
            "the header states no two-code-span pair count in a form this guard reads (digits, or "
            f"{_CLASS_EXAMPLES}..{_SPELLED_NUMERALS[-1]}) — the sentence was reworded or "
            "dropped, and neither this message nor `_TWO_SPAN_TOTAL` can tell which: re-point the pattern "
            "against how the count is published now, do not delete this arm"
        )
    elif two_span is not None and _as_int(total.group(1)) != len(two_span):
        # ⛔ Number AGREEMENT derived from the count, because the message quotes what "the header says" and
        # the pattern accepts `pairs?`: a one-pair edition was graded `the header says it writes one PAIRS`
        # — a statement about the header that the header does not make (`#14010` round-7 review, P2).
        _pair = "pair" if _as_int(total.group(1)) == 1 else "pairs"
        problems.append(
            f"the header says it writes {total.group(1)} {_pair} as two code spans, but this document "
            f"MEASURES {len(two_span)}"
        )
    uncounted = _TWO_SPAN_NEW.search(body)
    if not uncounted:
        problems.append(
            "the header states no count of two-code-span pairs the one-token rule skips in a form this "
            f"guard reads (digits, or {_CLASS_EXAMPLES}..{_SPELLED_NUMERALS[-1]}) — the "
            "sentence was reworded or dropped: re-point `_TWO_SPAN_NEW` against how it is published now, "
            "do not delete this arm"
        )
    elif extra_pairs is not None and _as_int(uncounted.group(1)) != extra_pairs:
        _verb = "is" if _as_int(uncounted.group(1)) == 1 else "are"
        problems.append(
            f"the header says `{uncounted.group(1)} of them` {_verb} not counted by the one-token rule, "
            f"but this document MEASURES {extra_pairs} of its {len(two_span)} two-code-span pairs as "
            f"not-already-counted"
        )
    return problems


def test_the_published_denominator_is_the_count_of_this_file() -> None:
    """The figures are COUNTED from the file they describe, not asserted as literals.

    `#13550`'s repair inherited `326 → 214 → 112 → 122` and shipped it green through a re-point that retired
    three pairs and wrote two: the written-spelling count is invariant under a re-point (326 either way),
    which is what the PR body verified — but the PAIR count is not, and `112 collisions` is arithmetic on it.
    A revert guard of literal strings cannot see that (`test_the_measured_figures_…` above spells out why),
    so the denominator is recomputed here from the document itself plus the tree, hermetically. It is the
    arm that re-measures the figures an integration moved: this merge took the document from
    `326 → 215 → 111 → 123` to `327 → 214 → 113 → 122`, and nothing but this recount could tell.

    ⚠️ That the scheme below is the HEADER's, and not a second one, is measurable against the header's own
    recorded history: it reproduces `314 → 217` at `43028d178b` and `320 → 216` at `295c26ebc8`, the two
    editions the paragraph at the end of this header re-measures — including the drifter numerators.

    NON-VACUOUS, measured on the pre-fix document (`git show 2b8df75c0e^:<the file>`, the edition whose
    header publishes `326 → 214 → 112`): this shipped scheme measures `326` spellings / `215` pairs /
    `111` collisions there, so `214`/`112` disagrees in both figures and this test reds on the tree it was
    written for. The `216`/`110` pair is a DIFFERENT edition's measurement, not a refuted one: this scheme
    returns `326`/`216`/`110` at `549bad0c77` and `b64290529b` (both publishing `326 → 214 → 112`), while
    the edition named above returns `326`/`215`/`111`. Each figure is measured with `_resolved_pairs` over
    the committed blob, not carried forward from the edition it names. The revision this replaces denied
    `216`/`110` as unreproducible "at any head" — two heads refute that (`#13653`), so the numbers stay and
    only the universal goes.
    `test_a_carried_forward_figure_is_reported` is the negative control for the comparator itself.
    """
    problems = _figure_problems((REPO / _AUDIT).read_text(encoding="utf-8"))
    assert problems == [], (
        "the audit header's published drift figures disagree with this file's own measurement:\n  "
        + "\n  ".join(problems)
        + "\nRe-measure by the header's rule (count the one-token `path.py:NNNN` spellings, resolve each, "
        "dedupe to `(path, start line)`, and compare the pin's line against the named `main`'s text for "
        "text) and update all of its sites together — never by applying a delta to the published number."
    )


def test_the_five_header_siblings_already_carry_their_pair_from_section_2_5() -> None:
    """The header's bare-sibling clause is only true while each of the five is ALSO written one-token.

    The clause now says a `path.py:NNNN` re-spelling would add "five more **as-written spellings** —
    345 → 350 — and so 114 → 119 collisions … while adding **no pair at all**: 231 either way, because
    each of the five already carries its pair from §2.5". That last half is the load-bearing one, and it
    was FALSE for the sentence it replaced: the previous edition claimed a pair spelling "would add five
    more pairs to the populations the figures above are measured over" (`#13991` review, P2-1), while
    measuring the same re-spelling with this file's own `_resolved_pairs` puts the delta at **0 pairs /
    +5 spellings** — the pairs do not move and the collisions do.

    That 0 is a set-membership fact, not an arithmetic one: a re-spelling adds no pair BECAUSE all five
    `(resolved path, line)` pairs are already in the population, from the §2.5 one-token sites. Delete
    ONE of those sites and this clause's promise goes false; no other arm checks THAT promise —
    deleting the site reds other arms, but each for its own reason (the spelling denominator moves, and
    the address's own pin loses its one occurrence), not for the pair-membership premise the clause
    asserts. So the premise is recovered from the document here rather than trusted to the prose.
    """
    _spellings, pairs = _resolved_pairs((REPO / _AUDIT).read_text(encoding="utf-8"))
    five = [(_SDK, 31551), (_SDK, 31533), (_SDK, 24513), (_SDK, 24857), (_MCP, 11590)]
    missing = [pair for pair in five if pair not in pairs]
    assert not missing, (
        f"the header's §2.5 `:Tag` enumeration says each of these addresses 'already carries its pair "
        f"from §2.5', but {missing} has no one-token `path.py:NNNN` spelling anywhere in the document — "
        "so re-spelling the bare header siblings WOULD add pairs and the clause's 'no pair at all' "
        "would be false. Restore the §2.5 one-token spelling, or correct the clause to the measurement."
    )


#: The census pairs the header's comparability split calls NON-comparable, with the pin-side line count that
#: makes each one so: the pair's line is past `ccb49741b2ec`'s copy of its file. MEASURED, one command per
#: file — `git show ccb49741b2ec:<path> | wc -l` — and the header publishes the same four counts in its
#: `wc -l` parenthetical, so the two are one measurement and this table is its per-pair form.
#:
#: ⛔ `mcp_server.py`'s `11590` is in this set because `main`'s `#14169` moved the address past the pin's
#: 11,466-line copy; it is NOT this branch's own write, which is why the header's split has to qualify
#: "this branch's own" rather than assert it for all of them. The `#14246` review's finding 1 is the exact
#: failure this table exists to make unreachable: the split shipped `224`/`ten`/`131`/`seven` — internally
#: consistent, and wrong — because nothing DERIVED it, and a self-consistent stale split cannot be caught
#: by arithmetic on its own four figures (224 + 10 == 234 and 131 + 7 == 138 both held).
_PIN_EOF_PAIRS: dict[tuple[str, int], int] = {
    ("tortoise/sdk.py", 57951): 56415,
    ("tortoise/sdk.py", 58099): 56415,
    ("tortoise/sdk.py", 58315): 56415,
    ("tortoise/sdk.py", 58317): 56415,
    ("tortoise/sdk.py", 58966): 56415,
    ("tortoise/sdk.py", 61999): 56415,
    ("tortoise/sdk.py", 62019): 56415,
    ("tortoise/search_engine.py", 7560): 7071,
    ("tortoise/search_engine.py", 7562): 7071,
    ("tortoise/hosted_api.py", 50935): 49851,
    ("tortoise/mcp_server.py", 11590): 11466,
}
#: The split's three published sentences. Each is matched on the header's emphasis-stripped body, like every
#: other figure pattern in this file, and each group is graded as a NUMBER (`_as_int`), so an edition may
#: state a count in digits or in words without the comparator reporting a false absence.
_SPLIT_COMPARABLE = re.compile(r"all (\d+) comparable ones resolve at BOTH heads")
_SPLIT_NONCOMPARABLE = re.compile(r"the (\w+) that are NOT comparable")
_SPLIT_DRIFT = re.compile(r"the drift is (\d+) of the (\d+) comparable, plus (\w+) of those (\w+) pairs")
_SPLIT_ABSENT_AT_MAIN = re.compile(r"the\s+(\w+) that do not exist at `main` either")


def test_the_comparability_split_names_every_pair_past_the_pins_eof() -> None:
    """The `N comparable / M not` split is derived from the pin's own file lengths, not trusted as prose.

    WHY THIS EXISTS. `#14246` re-pointed §2.5's `:Tag` traversal from `11281` to `11590`, which moved that
    `(path, line)` pair past `ccb49741b2ec`'s 11,466-line copy of `tortoise/mcp_server.py` — so the pair
    left the COMPARABLE population and joined the enumerated non-comparable ones. Every published figure
    moved with it (`224`/`ten`/`131 of the 224, plus seven of those ten` read `223`/`eleven`/`130 of the
    223, plus eight of those eleven`) and the headline total did not (`138` either way, because the pair's
    drifter status does not change — it exists at `main`). Nothing reddened: the split is a literal in the
    document and `_figure_problems` recomputes only the DENOMINATOR (spellings/pairs/collisions, which are
    invariant under a re-point). That is a published claim whose value could not vary under ANY mutation of
    the diff — the rule-3 uncovered-claim shape. The four figures happened to be internally consistent
    while wrong, so no arithmetic-only arm can catch this class; the pin's file lengths are what decides
    membership, and they are pinned here.

    WHAT IT GRADES. (1) Every pair in `_PIN_EOF_PAIRS` is still a census pair — a dropped or re-pointed cite
    must re-derive this table rather than leave it describing a population the document no longer has.
    (2) The header NAMES each of them at the address it writes, inside the non-comparable enumeration ("each
    named at the address this document writes for it" is the clause that promised it). (3) The published
    counts agree with the table: comparable + non-comparable == the pairs the file measures, and the four
    pinned `wc -l` values are the ones the header states for those files.

    NON-VACUOUS, measured on the pre-fix document (`git show 3cb2090c61:docs/architecture/…`): it reds on
    all three arms — the split publishes `224`/`ten` against this table's `223`/`11`, `mcp_server.py`'s
    `11590` is named nowhere in the enumeration, and the pin-side `wc -l` list has no entry for
    `mcp_server.py`.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    body = _unbolded(text)
    _spellings, pairs = _resolved_pairs(text)

    # (1) the table describes THIS document's population.
    orphan = sorted(pair for pair in _PIN_EOF_PAIRS if pair not in pairs)
    assert not orphan, (
        f"the comparability split counts {orphan} as non-comparable census pairs, but this document no "
        "longer writes a one-token spelling that resolves to them. Re-derive `_PIN_EOF_PAIRS` against the "
        "cites the document carries now — do not drop the entry to make the split agree with itself."
    )

    # (2) every one of them is NAMED where the header promises to name them: the non-comparable enumeration,
    # the sentence between "that are NOT comparable" and "past the pin's copy of its file". Either spelling
    # form the document uses for a pair counts — `SDK/FILE:NNNN` (`sdk.py:57951`) or the bare `:NNNN`
    # sibling behind an adjacent path (`search_engine.py`'s `:7560`).
    start = body.find("that are NOT comparable")
    end = body.find("past the pin's copy of its file")
    assert start != -1 and end > start, (
        "the header no longer publishes the non-comparable enumeration this split's figures describe "
        f"(`that are NOT comparable` at {start}, `past the pin's copy of its file` at {end}) — re-point "
        "this arm against how the split is published now, do not delete it"
    )
    region = body[start:end]
    unnamed = sorted(
        pair
        for pair in _PIN_EOF_PAIRS
        if not any(
            form in region
            for form in (
                f"`{pair[0]}:{pair[1]}`",
                f"`{pair[0].rsplit('/', 1)[-1]}:{pair[1]}`",
                f"`:{pair[1]}`",
            )
        )
    )
    assert not unnamed, (
        f"the comparability split counts {len(_PIN_EOF_PAIRS)} pairs as non-comparable but does not name "
        f"{unnamed} at the address the document writes for it. A pair that left the comparable set is a "
        "published figure moving: name it in that enumeration, and re-derive the counts the split states."
    )

    # (3) the published figures agree with the table and with each other.
    comparable = _SPLIT_COMPARABLE.search(body)
    noncomparable = _SPLIT_NONCOMPARABLE.search(body)
    drift = _SPLIT_DRIFT.search(body)
    absent = _SPLIT_ABSENT_AT_MAIN.search(body)
    for found, what in (
        (comparable, "`all N comparable ones resolve at BOTH heads`"),
        (noncomparable, "`the N that are NOT comparable`"),
        (drift, "`the drift is N of the M comparable, plus S of those T pairs`"),
        (absent, "`the N that do not exist at `main` either`"),
    ):
        assert found is not None, (
            f"the header no longer publishes {what} — the comparability split is a published claim, and "
            "this arm cannot grade a sentence it cannot read. Re-point the pattern against how it is "
            "written now, do not delete the arm"
        )
    expected_comparable = len(pairs) - len(_PIN_EOF_PAIRS)
    assert int(comparable.group(1)) == expected_comparable, (
        f"the header says `{comparable.group(1)}` comparable pairs, but this document measures {len(pairs)} "
        f"pairs and `_PIN_EOF_PAIRS` holds {len(_PIN_EOF_PAIRS)} past the pin's EOF — so {expected_comparable} "
        "are comparable. Re-measure the split, do not adjust the table to it."
    )
    assert _as_int(noncomparable.group(1)) == len(_PIN_EOF_PAIRS), (
        f"the header says `{noncomparable.group(1)}` pairs are NOT comparable, but `_PIN_EOF_PAIRS` — the "
        f"pin-side facts, `git show ccb49741b2ec:<path> | wc -l` against each pair's line — measures "
        f"{len(_PIN_EOF_PAIRS)}. A pair that crossed the pin's EOF is the whole delta here."
    )
    comparable_denominator = int(drift.group(2))
    assert comparable_denominator == expected_comparable, (
        f"the drift sentence names `of the {comparable_denominator}` comparable pairs while the split's own "
        f"denominator measures {expected_comparable} — the ratio has to name the population it was taken over"
    )
    # The incomparable half splits in two: the pairs that DO exist at the named `main` (and so drift) and the
    # ones that exist at neither head (and so cannot be compared at all). Both counts are published, and they
    # must add up to the population `_PIN_EOF_PAIRS` measures — which is what makes "eight of those eleven"
    # checkable rather than asserted.
    drifters_incomparable = _as_int(drift.group(3))
    absent_at_main = _as_int(absent.group(1))
    assert drifters_incomparable + absent_at_main == len(_PIN_EOF_PAIRS), (
        f"the split says {drifters_incomparable} of its non-comparable pairs drift and {absent_at_main} "
        f"exist at neither head — {drifters_incomparable + absent_at_main} in all, but the pin-side facts "
        f"measure {len(_PIN_EOF_PAIRS)} non-comparable pairs. Every one of them is in exactly one half."
    )
    assert _as_int(drift.group(4)) == len(_PIN_EOF_PAIRS), (
        f"the drift sentence names `of those {drift.group(4)} pairs` while the split counts "
        f"{len(_PIN_EOF_PAIRS)} non-comparable pairs"
    )
    # The pinned `wc -l` values ARE the membership rule, so the header has to state the one for every file a
    # non-comparable pair lives in — the pin-side fact a reader checks, and the entry `#14246` was missing.
    for (path, _line), pin_lines in _PIN_EOF_PAIRS.items():
        stated = (
            f"{path} | wc -l` -> `{pin_lines}`" in body
            or f"…/{path.rsplit('/', 1)[-1]}` -> `{pin_lines}`" in body
        )
        assert stated, (
            f"the split's non-comparable membership rests on `{path}` being {pin_lines} lines at the pin, "
            "but the header's pin-side `wc -l` parenthetical does not state it. Without that figure the "
            "five numerals above are unverifiable prose, which is how they shipped wrong."
        )


def test_a_carried_forward_figure_is_reported() -> None:
    """The comparator's negative case, reachable: a stale triple and a broken arithmetic relation red.

    ⛔ Every mutation below is DERIVED FROM THE SHIPPED SENTENCE, never written as a literal. This control
    has now had to be re-pointed TWICE: it was written against `326`/`215`/`111`/`123`, and the merge that
    took `main`'s `#13551` edition replaced every one of those sources — so all five `assert mutated != text`
    fired FIRST, and the control reported "the string was not found" without ever reaching the comparator it
    exists to exercise (`#13599` review, finding 2: the exact unreachability it was written to prevent, one
    level up). Re-pointing the five literals at `327`/`214`/`113`/`122` fixes this head and schedules the
    same failure for the next re-measurement, which is the one thing this file must not do: a figure quoted
    from the document it guards rots the moment the document is re-measured.

    What cannot rot is the RELATION each mutant breaks: a pair count one under the measured one, an
    arithmetic figure that stops following its pair count, a ratio naming a denominator it does not carry,
    one of the three mentions of the ratio disagreeing with the others, an alternative basis level with the
    primary, an alternative basis whose numerator is inflated past what its added pairs can
    contribute, a two-code-span pair count stated below what the document measures, and one more of those
    pairs claimed uncounted than the union adds. Each is applied to the sentence AS SHIPPED, by
    capture-group position.

    ⛔ The alternative basis is mutated on BOTH capture groups (`:1` pairs, `:2` drifters) on purpose: the
    drifter half used to be read and thrown away (`_alt_drifters`), so the guard on the only figure in this
    sentence without a second witness was never exercised by the control that exists to prove it works —
    which is how a one-sided bound survived a round that found two other reachability defects here.
    """
    raw = (REPO / _AUDIT).read_text(encoding="utf-8")
    assert _figure_problems(raw) == [], "the shipped document must measure clean before it is mutated here"
    text = _unbolded(raw)
    triple = _TRIPLE.search(text)
    quadruple = _QUADRUPLE.search(text)
    ratio = re.search(r"(\d+) of this document's (\d+)", text)
    alt = _ALT_BASIS.search(text)
    two_total = _TWO_SPAN_TOTAL.search(text)
    two_new = _TWO_SPAN_NEW.search(text)
    for found, what in ((triple, "triple"), (quadruple, "figures sentence"), (ratio, "`N of this document's M`"),
                        (alt, "alternative basis"), (two_total, "two-code-span pair count"),
                        (two_new, "two-code-span uncounted count")):
        assert found is not None, (
            f"the shipped document no longer carries the {what}, so this control has nothing to mutate — "
            "re-point `_TRIPLE`/`_QUADRUPLE`/`_ALT_BASIS`/`_TWO_SPAN_TOTAL`/`_TWO_SPAN_NEW` at how it is "
            "published now"
        )

    def _mutate(match: re.Match[str], overrides: dict[int, int]) -> str:
        """The matched sentence with capture group *k* replaced by `overrides[k]`, positionally.

        Splitting the SENTENCE on its digits is what makes this positional: `214` occurs twice in the
        figures sentence (the pair count and the ratio's own denominator) and `327` again in the header
        line, so a `str.replace` would carry the same edit into a group nobody meant to touch and prove
        nothing about either.
        """
        parts = re.split(r"(\d+)", match.group(0))
        for index in range(1, len(parts), 2):
            group = index // 2 + 1
            if group in overrides:
                parts[index] = str(overrides[group])
        return "".join(parts)

    triple_pairs, triple_collisions = map(int, triple.groups()[1:])
    _written, _pairs, collisions, _drifters, comparable = map(int, quadruple.groups())
    ratio_drifters, _ratio_pairs = map(int, ratio.groups())
    alt_pairs, alt_drifters = map(int, alt.groups())
    two_span_stated = _as_int(two_total.group(1))
    two_span_uncounted = _as_int(two_new.group(1))

    for match, overrides, why in (
        (
            triple,
            {2: triple_pairs - 1, 3: triple_collisions - 1},
            "the state this repair inherited: a pair count and its collision arithmetic one edition behind",
        ),
        (
            quadruple,
            {3: collisions + 1},
            "collisions stops following the pair count it is arithmetic on",
        ),
        (
            quadruple,
            {5: comparable - 1},
            "the ratio names a denominator it does not carry",
        ),
        (
            ratio,
            {1: ratio_drifters - 1},
            "one of the three mentions of the ratio disagrees with the others",
        ),
        (
            alt,
            {1: alt_pairs - 1},
            "the alternative basis is one pair short of what the two-code-span form measures, not level with it",
        ),
        (
            alt,
            {2: alt_drifters + 7},
            "the alternative basis's numerator is overstated past what its added pairs can contribute — the "
            "direction a stale figure takes when a denominator moves under it",
        ),
        (
            alt,
            {2: _drifters - 1},
            "the alternative basis reads FEWER drifters than the primary — adding pairs cannot remove one "
            "(the direction the one-sided bound used to be the only guard for, never exercised; the mutant "
            "is anchored to the PRIMARY numerator, not `alt - 2`, because a band wide enough for four added "
            "pairs would swallow `alt - 2` and the control would silently stop proving anything)",
        ),
        (
            two_total,
            {1: _two_span_total_mutant(two_span_stated)},
            "the two-code-span pair count DISAGREES with what the document measures — below it at the "
            "shipped P2-A edition (`three` stated where five are written), which leaves the published "
            "`227` unreachable from the `223` beside it and turns the reconciliation into arithmetic a "
            "reader cannot close; above it when a below value would not be a count the pattern reads "
            "(`_two_span_total_mutant`), so the mutant always lands on the COUNT arm rather than on the "
            "absent-sentence arm",
        ),
        (
            two_new,
            {1: two_span_uncounted + 1},
            "one more two-code-span pair is claimed uncounted than the union actually adds, so the stated "
            "reconciliation overstates what the alternative basis contributes",
        ),
    ):
        # ⛔ Both spell their numeral, so they go through the numeral-aware mutator; `_mutate` would
        # split on digits that are not there and return the sentence unchanged, which its own
        # `stale != match.group(0)` assertion would then catch as a broken control rather than a pass.
        stale = (
            _mutate_numeral(match, next(iter(overrides.values())))
            if match in (two_total, two_new)
            else _mutate(match, overrides)
        )
        assert stale != match.group(0), f"the negative control for {why!r} did not change the figures"
        mutated = text.replace(match.group(0), stale, 1)
        assert mutated != text, f"the negative control for {why!r} did not change the text"
        if match in (two_total, two_new):
            # ⛔ RULE 2, ENFORCED HERE RATHER THAN ASSUMED: this mutant's red has to come from the COUNT
            # comparison, and the only observation below is `_figure_problems(mutated) != []`, which the
            # ABSENT-SENTENCE arm satisfies just as well. A value the pattern cannot parse (the old fixed
            # `stated - 2`, which renders `-1` at an honest edition measuring one pair) therefore passed
            # the control while the comparator it exists to prove was never consulted — a false PASS of the
            # control (`#14010` round-4 finding 1). Asserting the mutated sentence still parses makes that
            # state a red HERE, whatever the mutant value is.
            arm = _TWO_SPAN_TOTAL if match is two_total else _TWO_SPAN_NEW
            rematched = arm.search(_unbolded(mutated))
            assert rematched is not None, (
                f"the control for {why!r} produced {stale!r}, which its own pattern cannot read, so the "
                "red would come from the absent-sentence arm and the count comparison would stay unproven "
                "— fix the mutant VALUE, do not relax this control"
            )
            assert _as_int(rematched.group(1)) == next(iter(overrides.values())), (
                f"the control for {why!r} rewrote the count the pattern grades, not its own figure: "
                f"{rematched.group(1)!r} vs {next(iter(overrides.values()))!r}"
            )
        assert _figure_problems(mutated) != [], (
            f"a document carrying {stale!r} ({why}) reported no problem — the comparator is decoration"
        )


def test_the_numeral_mutator_edits_the_stated_count_whatever_numeral_it_spells() -> None:
    """`_mutate_numeral` mutates the numeral its own patterns accept, in every spelling they accept.

    ⛔ Its positional guard used to be `match.group(0).count(token) == 1`, and that is FALSE BY
    CONSTRUCTION for two of the numerals `_TWO_SPAN_TOTAL`/`_TWO_SPAN_NEW` accept — on CORRECT content:

    * `writes TWO pairs as TWO code spans` — `_TWO_SPAN_TOTAL`'s own scaffold spells `two`, so a document
      measuring two two-code-span pairs (`count("two") == 2`) tripped the assert;
    * `ONE of them is resolved but not counted by the ONE-token rule` — the `one-token rule` is part of
      the sentence, so `count("one") == 2` on the (entirely plausible) reading that one of the pairs is
      not already counted. (`one` is the only count that takes the SINGULAR verb — `_TWO_SPAN_NEW`
      accepts `(?:is|are)` on purpose, for this sentence in the form a lane would actually write it.)

    Both are legitimate measurements of this document — the patterns accept `_SPELLED_NUMERALS` and
    `_as_int` grades all of them — so the helper rejected numerals the rest of the guard is built to
    accept. The one trigger it had was a false red on correct text, which is the class this file's own
    header exists to
    prevent (`#13599` review, finding 1: a comparator that reds on CORRECT text), and it pointed the
    next re-measuring lane at the control's token bookkeeping rather than at their measurement.

    NON-VACUOUS, measured: with the count assert in place this test reds with `the numeral two occurs
    twice in 'writes two pairs as two code spans'` on the `two` case — the FIRST numeral in the iteration
    that also occurs in the frame it is substituted into (`zero`, `none` and `one` all keep
    `count == 1`, in `writes one pair …`), not on the first case tried; with the positional assert both
    cases mutate their own capture group and re-match. It iterates the WHOLE accepted class, not the
    two instances, because a guard written against one spelling of one numeral is the defect above — and
    the class it iterates is DERIVED from `_NUMERALS`, so extending the vocabulary (the `eleven`/`zero`
    end, `#14010` round 4) extends the pin in the same edit instead of leaving a hand-copied tuple to stop
    at `ten` while the patterns accept more.
    """
    for pattern, template in (
        (_TWO_SPAN_TOTAL, "the header writes {n} {pair} as two code spans"),
        (_TWO_SPAN_NEW, "{n} of them {verb} resolved but not counted by the one-token rule"),
    ):
        for numeral in (*_NUMERALS, "7", "1"):
            # Number AGREEMENT, from the count this numeral stands for: `one pair … IS`, `zero pairs …
            # ARE`. Spelling the wrong one would test a frame the patterns are not required to accept,
            # and the singular frame is exactly the one a one-pair edition writes.
            singular = _as_int(numeral) == 1
            sentence = template.format(
                n=numeral, pair="pair" if singular else "pairs", verb="is" if singular else "are"
            )
            match = pattern.search(sentence)
            assert match is not None, f"the pattern no longer matches its own sentence frame: {sentence!r}"
            assert match.group(1) == numeral, (match.group(0), numeral)
            mutated = _mutate_numeral(match, 9)
            assert mutated == match.group(0).replace(numeral, "9", 1), (
                f"mutating `{numeral}` in {match.group(0)!r} landed on the wrong occurrence: {mutated!r}"
            )
            rematched = pattern.search(mutated)
            assert rematched is not None and rematched.group(1) == "9", (
                f"the mutated sentence {mutated!r} no longer re-matches with its new count — the edit did "
                "not land on the numeral the pattern grades"
            )


def test_the_numeral_mutators_offset_assert_can_fire() -> None:
    """The offset assert has a red of its own, on the one shape that violates it.

    ⛔ Both patterns `_mutate_numeral` serves capture at the numeral's own offset on EVERY input they accept
    (`_TWO_SPAN_NEW` at 0; `_TWO_SPAN_TOTAL` at 7, behind a literal prefix that contains no numeral), so no
    document can violate the assertion and DELETING it left the whole suite green (`#14010` round 3,
    finding 2 — a guard no mutation can redden is dead weight under #3442 rule 3). The property it states
    is still the one the edit depends on (`str.replace(…, 1)` rewrites the LEFTMOST occurrence), so the
    repair is to reach it, not to delete it: this synthetic pattern captures the second `two`, which is the
    exact state the assertion exists to refuse.
    """
    synthetic = re.compile(r"(?:two )?writes (two) pairs as two code spans")
    match = synthetic.search("two writes two pairs as two code spans")
    assert match is not None and match.group(1) == "two"
    try:
        _mutate_numeral(match, 9)
    except AssertionError as exc:
        assert "not at the captured numeral's offset" in str(exc), (
            f"the offset assert fired, but not with its own message: {exc}"
        )
    else:
        raise AssertionError(
            "`_mutate_numeral` accepted a match whose captured numeral is NOT its leftmost occurrence: "
            "the leftmost rewrite would have edited a different word than the one the pattern grades, and "
            "the positional precondition is unenforced"
        )


def test_the_two_span_count_mutant_always_lands_on_the_count_arm() -> None:
    r"""Every measurement this document can legitimately reach gets a mutant the count arm can READ.

    ⛔ The control asserts only `_figure_problems(mutated) != []`, so a mutant value the count pattern
    cannot parse still passes — on the ABSENT-SENTENCE arm, with the comparison it exists to prove never
    consulted (a false PASS of the control, `#14010` round-4 finding 1). The fixed `stated - 2` did exactly
    that at an honest edition measuring one two-code-span pair: `-1` is not a token `\d+` reads after
    `writes `, so no sentence the store can hold carried this mutant onto the count arm.

    NON-VACUOUS: with `_two_span_total_mutant` replaced by the `stated - 2` it replaces, this reds on its
    first uncovered measurement (`stated=0` renders `writes -2 pairs…`, which the pattern does not match) —
    a measurement the shipped document does not have today, which is exactly why the control could not see
    it and why the property is pinned here rather than sampled. The loop starts at `0` and not at `1`, so
    `-2` is the first value it meets, not `-1` (`#14010` round-5 review, P2: the sentence named the second).
    """
    for stated in range(0, len(_SPELLED_NUMERALS) + 3):
        value = _two_span_total_mutant(stated)
        sentence = f"the header writes {value} pairs as two code spans"
        match = _TWO_SPAN_TOTAL.search(sentence)
        assert match is not None, (
            f"a document stating {value} pairs (the mutant for a stated {stated}) is not a sentence "
            "`_TWO_SPAN_TOTAL` can read, so the control would pass on the absent-sentence arm"
        )
        assert _as_int(match.group(1)) == value, (match.group(0), value)
        assert value != stated, f"the mutant for a stated {stated} states {value} — it proves nothing"
        assert value >= 0, f"the mutant for a stated {stated} is negative: {value}"


def test_the_accepted_numeral_class_is_pinned_by_literals_not_by_the_table_it_derives() -> None:
    r"""The spelled vocabulary the two sentences may state a count in, pinned WITHOUT deriving it.

    ⛔ Every other row that touches this class derives its iteration set from `_NUMERALS`, and the two
    not-found messages derive their examples from the same table — so those rows can only ever AGREE with
    whatever the table says. They could not report the round-4 fix being reverted: truncating
    `_SPELLED_NUMERALS` back to the pre-fix ceiling of `ten` left all six new rows GREEN (measured,
    `#14010` round-6 review, finding 1), which would reinstate the `#13599` false-absence class silently —
    a correct `writes eleven pairs as two code spans` reported as a MISSING sentence.

    The expectations here are LITERALS on purpose: `zero`/`none` because an honest re-measurement can find
    nothing left to disclose, `one` because it is the only count that takes the singular verb, and
    `eleven`/`twenty` because they are past the ceiling the defect stopped at. Narrowing the vocabulary
    now reds by name (`eleven`), and widening it still extends the derived sweep beside this test.
    """
    for spelling, value in (
        ("zero", 0),
        ("none", 0),
        ("one", 1),
        ("ten", 10),
        ("eleven", 11),
        ("twenty", 20),
    ):
        pair, verb = ("pair", "is") if value == 1 else ("pairs", "are")
        total = _TWO_SPAN_TOTAL.search(f"the header writes {spelling} {pair} as two code spans")
        assert total is not None and total.group(1) == spelling, (
            f"`{spelling}` is a count this header may state as a word, but `_TWO_SPAN_TOTAL` does not read "
            "it — the pattern's ceiling and `_NUMERALS` have come apart again; a correct sentence would be "
            "graded as a missing one"
        )
        uncounted = _TWO_SPAN_NEW.search(
            f"{spelling} of them {verb} resolved but not counted by the one-token rule"
        )
        assert uncounted is not None and uncounted.group(1) == spelling, (
            f"`{spelling}` is a count this header may state as a word, but `_TWO_SPAN_NEW` does not read it"
        )
        assert _as_int(spelling) == value, (
            f"the patterns read `{spelling}` but `_as_int` grades it {_as_int(spelling)}, not {value} — the "
            "grader and the patterns are one vocabulary or they are a defect"
        )

    # ⛔ EVERY value, not the six spellings exercised above: those come from an `enumerate` over a
    # hand-ordered tuple, so a transposition (`six`/`seven`, `sixteen`/`seventeen`) renumbers every count
    # after it and each derived row agrees with the table by construction. Measured before this assertion:
    # swapping two entries left all rows GREEN while a correct `writes seventeen pairs as two code spans`
    # was graded `16` (`#14010` round-7 review, P2 — the round-5 defect one spelling over). The equality is
    # deliberately BOTH-WAYS: it reds on a narrowed class, a widened class and a reordered one.
    assert _NUMERALS == {
        "zero": 0,
        "none": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
    }, f"the graded vocabulary is not the one this file documents: {_NUMERALS!r}"

    # ⛔ The LEFT boundary of both patterns, pinned. The trailing literal binds the right edge only, so
    # without `\b` the arms read a count out of a sentence that is not the disclosure — `rewrites five
    # pairs…` matches `writes five pairs…`, `everyone of them …` matches `one of them …`. Both arms grade
    # the LEFTMOST match, so such a sentence earlier in the document can mask a genuinely missing
    # disclosure (a false PASS) or grade the wrong one (a false red). Measured: dropping either `\b` reds
    # exactly this assertion (`#14010` round-6 review, P2 — the fix shipped without a pin).
    assert _TWO_SPAN_TOTAL.search("the recount rewrites five pairs as two code spans") is None, (
        "`writes` matched inside `rewrites` — the leading boundary is gone"
    )
    assert _TWO_SPAN_NEW.search(
        "everyone of them are resolved but not counted by the one-token rule"
    ) is None, "`one` matched inside `everyone` — the leading boundary is gone"
    assert _TWO_SPAN_TOTAL.search("this header also writes five pairs as two code spans") is not None, (
        "the boundary must not cost the shipped phrasing (`also writes five pairs`)"
    )
    assert _TWO_SPAN_NEW.search(
        "four of them are resolved but not counted by the one-token rule"
    ) is not None, "the boundary must not cost the shipped phrasing (`four of them`)"


def test_a_reworded_figures_sentence_is_graded_not_crashed_by_path_resolution() -> None:
    r"""A header whose `_TRIPLE`/`_QUADRUPLE` sentence moved gets its problem STRING, not an assertion.

    ⛔ `_two_span_pairs` resolves every written path through `_resolve_path`, whose ambiguity check is an
    `AssertionError`. Hoisted above the two early returns that grade a reworded figures sentence as a plain
    problem, that resolution ran on a header the function never needed it for, so a re-measurement that
    reworded `dedupes the N …` — the case this function is written to REPORT — died on a traceback from an
    unrelated cite instead (`#14010` final review, finding 3). The call is below those returns now, and this
    test's fixture carries an unresolvable two-code-span cite AND no figures sentence.

    ⛔ THE HOIST IS NOW AN EQUIVALENT MUTANT, and that is recorded here rather than left as a claim the code
    no longer supports (`#3442` rule 3). Round 6 made a resolution failure GRADED (`except AssertionError`
    → a problem string), so a hoisted call no longer raises: it appends a problem that the early `return`
    then discards, and this test goes GREEN. Measured at this head — the hoist mutant is 24/24 green — so
    this test can NOT tell the two orders apart any more. What it still pins is the property it is named
    for: this state comes back as a GRADED problem, never a traceback. The crash path itself is pinned by
    `test_a_two_span_cite_that_does_not_resolve_is_graded_not_raised`. The order is still kept — a header
    whose figures sentence is gone never pays for the resolver.
    """
    text = (
        "the header writes one pairs as two code spans and cites `no_such_module_zzz.py` `:1`, "
        "with no figures sentence in this text"
    )
    problems = _figure_problems(text)
    assert problems and "no longer publishes" in problems[0], (
        "a header whose figures sentence was reworded must come back as a graded problem — getting an "
        f"`AssertionError` out of `_resolve_path` instead means the two-code-span measurement is running "
        f"before the early returns that own this state; got {problems!r}"
    )


def test_a_two_span_cite_that_does_not_resolve_is_graded_not_raised() -> None:
    r"""A header whose two-code-span cite no longer resolves gets a problem STRING, not a traceback.

    ⛔ `_two_span_pairs` resolves every written path through `_resolve_path`, whose ambiguity check is an
    `AssertionError`, and a header re-measured against a tree where a cite moved is EXACTLY the state this
    function exists to grade. The call was moved below the `_TRIPLE`/`_QUADRUPLE` early returns (`#14010`
    final review, finding 3) but still ran above `if not alt`, so a header that had also lost the
    alternative-basis sentence — a state graded as a plain problem two arms away — died on the cite instead
    (`#14010` round-6 review, finding 2). The resolution failure is caught and GRADED now, and the arms
    that would need its value are skipped rather than fed a zero: `extra_pairs` is `None` (unknown), not
    `0` (a figure this guard never measured), which is what keeps the count comparisons from publishing a
    wrong number (the `#13599` finding-1 class, opposite direction).

    NON-VACUOUS: with the `try`/`except` removed this test does not fail an assertion — it ERRORS out of
    `_figure_problems`, which is the defect stated as a red.
    """
    document = (REPO / _AUDIT).read_text(encoding="utf-8")
    body = _unbolded(document)
    total = _TWO_SPAN_TOTAL.search(body)
    alt = _ALT_BASIS.search(body)
    assert total is not None and alt is not None, "the shipped document must carry both sentences mutated here"
    # Keep every OTHER sentence intact (so the `_TRIPLE`/`_QUADRUPLE` early returns do not own this state,
    # and the alternative-basis arm is reached), and move the two-code-span cite onto a path that resolves
    # to no file at all.
    text = body.replace(
        total.group(0), "writes six pairs as two code spans and cites `no_such_module_zzz.py` `:1`", 1
    )
    problems = _figure_problems(text)
    unresolved = [p for p in problems if "cannot be resolved against this tree" in p]
    assert unresolved, (
        "a header whose two-code-span cite resolves to no file must come back as a graded problem; an "
        f"`AssertionError` out of `_resolve_path` means the comparator crashes on the state it grades. "
        f"got {problems!r}"
    )
    assert "no_such_module_zzz.py" in unresolved[0], (
        f"the graded problem must NAME the cite that did not resolve: {unresolved[0]!r}"
    )
    measured = [p for p in problems if "MEASURES" in p and "two code spans" in p]
    assert not measured, f"an unresolvable two-span count must not be graded against a fallback figure: {measured!r}"


def test_an_unread_two_span_count_is_reported_as_UNREAD_not_as_absent() -> None:
    """The two-code-span arms say the count is not in a readable form, never that the sentence is gone.

    ⛔ Both messages used to assert *"the header no longer states how many pairs…"* on ANY unparsed input.
    That was FALSE for the one state the guard was built to survive: a correct sentence using a form the
    pattern did not read (`zero`, `none`, `eleven` — all of them honest re-measurements of this very
    header) came back as a MISSING-SENTENCE verdict, the class `#13599` finding 1 punished and `#14010`
    rounds 3 and 4 both blocked on. The vocabulary above is derived now so those particular forms read, but
    the message must not re-acquire the false claim for whatever form the NEXT re-measurement invents.

    NON-VACUOUS: against the previous wording BOTH frames red — on the `assert unread` line, because that
    wording contained no `in a form this guard reads` clause at all, so `unread` came back empty. The
    `all(...)` clause below is the durable guard on the message TEXT rather than on that one revert: it
    reds if a future rewording mixes the two claims in a message that still passes the `unread` filter.
    ⛔ Each frame substitutes with ITS OWN compiled pattern, not a shared alternation. Under `count=1` a
    shared alternation replaces the FIRST match in the document — always the `writes … pairs as two code
    spans` line, which comes first — so the `_TWO_SPAN_NEW` iteration used to rewrite the OTHER arm's
    sentence, leave its own intact, and have its assertions satisfied by the other arm's message: a
    vacuous frame that stayed green when `_TWO_SPAN_NEW`'s wording was reverted (`#14010` round-5 review,
    P2). `assert marker in unread[0]` is what makes each iteration speak about its own arm.
    ⛔ The search and the substitution both run on the UNBOLDED body — the surface `_figure_problems`
    grades. Against the raw text, a correct edition that bolds the count (`writes **five** pairs as two code
    spans`) matched nothing here, so this test redded on CORRECT content with its own false-absence message —
    the exact class it exists to refuse (`#14010` round-7 review, P1).
    """
    document = _unbolded((REPO / _AUDIT).read_text(encoding="utf-8"))
    for arm, sentence, frame, marker in (
        (
            _TWO_SPAN_TOTAL,
            "writes a handful of pairs as two code spans",
            "writes a handful of pairs as two code spans",
            "two-code-span pair count",
        ),
        (
            _TWO_SPAN_NEW,
            "a handful of them are resolved but not counted by the one-token rule",
            "a handful of them",
            "the one-token rule skips",
        ),
    ):
        assert arm.search(document) is not None, "the shipped document must carry the sentence mutated here"
        mutated = arm.sub(sentence, document, count=1)
        assert mutated != document
        assert arm.search(mutated) is None, (
            f"the {frame!r} frame must be GONE from the mutated document — {arm.pattern!r} still matches, so "
            "the substitution landed on a different sentence"
        )
        problems = _figure_problems(mutated)
        assert problems, f"a header carrying {frame!r} was reported clean"
        unread = [p for p in problems if "in a form this guard reads" in p]
        assert unread, (
            f"a header carrying {frame!r} must be reported as a count this guard cannot READ (the accepted "
            f"class named in the message); got {problems!r}"
        )
        assert marker in unread[0], (
            f"the {frame!r} frame must be reported by ITS OWN arm — expected {marker!r} in the message, got "
            f"{unread[0]!r}; a message from the other arm means the substitution landed on the wrong "
            "sentence and this frame is vacuous"
        )
        assert all("no longer states" not in p for p in problems), (
            "a message asserts the sentence is GONE, which is false for a sentence that is present in a "
            f"form the pattern does not read: {problems!r}"
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


_JSONL_DOC = "docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md"
#: The first line of `_emit_event`'s JSONL half — the paragraph §6.3 item 1 quotes.
_JSONL_QUOTE = "**JSONL event log (#548):** written when *point* is provided"


def _cited_span_ends(doc_text: str, start: int) -> list[int]:
    """Every END a `sdk.py:START-END` span opening at *start* states in *doc_text*."""
    return [
        int(match.group(1))
        for match in re.finditer(rf"sdk\.py:{start}-(\d+)", doc_text)
    ]


def test_the_jsonl_contract_span_ends_on_its_paragraphs_last_line() -> None:
    """A span cite must END on the last line of the paragraph it quotes, not one short.

    #14087: §6.3 item 1 cited `sdk.py:16077-16081` while the contract's paragraph runs through
    `:16082`, with `:16083` blank. A `_PINS` row pins a span's START only, so the short END was
    green in every other test in this file — measured on a throwaway copy, moving the END back one
    line left the whole suite passing. Derived, not hardcoded: the START is the line carrying the
    quoted fragment, the END is the last line before the paragraph's blank terminator.
    """
    lines = (REPO / "tortoise/sdk.py").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines, 1) if _JSONL_QUOTE in line)
    end = start
    while end < len(lines) and lines[end].strip():  # `lines[end]` is the line AFTER `end`
        end += 1
    assert end > start, (
        f"`sdk.py:{start}` is a one-line paragraph, so this arm would grade nothing — re-point "
        "`_JSONL_QUOTE` at the contract it is meant to cover."
    )
    doc_text = (REPO / _JSONL_DOC).read_text(encoding="utf-8")
    cited = _cited_span_ends(doc_text, start)
    assert cited == [end], (
        f"§6.3 cites `sdk.py:{start}-{cited[0] if cited else '???'}` but that paragraph's last line is "
        f"`:{end}` — a span "
        "must end on it, not one short (`_PINS` pins the START, so the short END is green everywhere "
        "else in this file)"
    )
    for mutant in (end - 1, end + 1):  # NON-VACUOUS: one short and one long both red
        tampered = doc_text.replace(f"sdk.py:{start}-{end}", f"sdk.py:{start}-{mutant}")
        assert _cited_span_ends(tampered, start) != [end], (
            f"a span end moved to `:{mutant}` still passed this arm"
        )


#: The numeric span a cite token names: `:START` or `:START-END`.
_TOKEN_SPAN = re.compile(r":(\d+)(?:-(\d+))?")


def _token_spans(token: str) -> list[tuple[int, int]]:
    """EVERY address the *token* names, as `(first, last)` spans — empty when it names none.

    One span per address match, never the hull of a token's numbers: a token naming two
    unrelated addresses (`:58315`/`:58317`) names two lines, NOT the line between them. Taking
    `min..max` over every number turned such a token into a range, so the arm accepted the `58316`
    the token never mentions — the exact false PASS this arm exists to refuse, one indirection
    further out. Measured on the shipped tree: **0 of 96** rows carries more than one address, so
    no row's verdict moves; only the door the next row could have walked through is closed.
    """
    return [
        (int(match.group(1)), int(match.group(2) or match.group(1)))
        for match in _TOKEN_SPAN.finditer(token)
    ]


def _rows_outside_their_token(rows) -> list[str]:
    """Rows whose graded line is NOT inside the address the row's OWN token names.

    *rows* is either `_PINS`' 5-tuples or `_MAIN_SET_MISSED`' 4-tuples; only the token, the
    target and the graded line are read.
    """
    problems: list[str] = []
    for row in rows:
        token, target_rel, line = (
            (row[1], row[2], row[3]) if len(row) == 5 else (row[0], row[1], row[2])
        )
        spans = _token_spans(token)
        if not spans:
            # A row whose token names NO address cannot be checked by this arm, so passing it
            # silently would be the arm vouching for a claim it never read (fail-open).
            problems.append(
                f"{target_rel}: the row pins `{token}`, which names NO line address, so this "
                f"arm cannot check the line it GRADES ({line}) — an unverifiable row passes by "
                "being unreadable, which is a fail-open in a gate. Give the token the address "
                "the fragment sits on."
            )
            continue
        if any(first <= line <= last for first, last in spans):
            continue
        named = " / ".join(
            f"{first}" if first == last else f"{first}..{last}" for first, last in spans
        )
        problems.append(
            f"{target_rel}: the row pins `{token}`, which names {named}, but it "
            f"GRADES line {line} — a graded line outside its own token is a DEFENDER: the "
            "content arm opens the line the fragment lives on while the token arm only asks "
            "whether the string occurs anywhere in the document, so a document that names a "
            "stale address for that line stays green. Re-point the DOCUMENT to the address the "
            "fragment actually sits on, then re-point this row to the same number."
        )
    return problems


def test_every_pinned_row_grades_a_line_inside_the_address_its_token_names() -> None:
    """The token IS the claim: a row that grades some other line defends a stale cite.

    MEASURED, and shipped in the reviewed tree: two rows pinned a STALE address while grading the
    CORRECT line — `("`:31496`", `sdk.py`, `31551`)` for the §2.5 `:Tag` header entry and
    `("`:37534` in `_file_human_approval`", `sdk.py`, `37547`)` in the substrate map. Both arms were
    green: `_resolve`'s content arm opened `31551` / `37547` (where the fragment is), and its token
    arm only asks whether the token string occurs SOMEWHERE in the document (it did). So the
    document could name an address 55 lines above the statement it describes — the `#13290` class this
    file exists to stop — with nothing red, and the four `:31496`-family rows were in fact added BY a
    review as coverage.

    A RANGE is anchored on the line inside it that carries the fragment (the file's stated
    convention), so the arm accepts any line in `START..END`; a single-value token must grade that
    value exactly, and a token naming SEVERAL addresses accepts only a line one of them names —
    never the hull between them, and never nothing at all (the two fail-open doors the controls
    below re-open).

    Non-vacuity is the negative control below, which re-introduces the shipped shape.
    """
    problems = _rows_outside_their_token(_PINS) + _rows_outside_their_token(_MAIN_SET_MISSED)
    assert not problems, "\n".join(problems)


def test_the_token_span_arm_reds_on_a_row_that_grades_another_line() -> None:
    """Negative control — the SHIPPED pre-fix shape: stale token, correct line, green."""
    stale = (_AUDIT, "`:31496`", _SDK, 31551, "MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag})")
    problems = _rows_outside_their_token((stale,))
    assert problems, "a token naming `:31496` must not be allowed to grade line 31551"
    assert "31496" in problems[0] and "31551" in problems[0], problems[0]
    # The substrate-map row the same review found, in its shipped form.
    shipped = (_SUBSTRATE, "`:37534` in `_file_human_approval`", _SDK, 37547, "``:Document`` node).")
    assert _rows_outside_their_token((shipped,)), "the `:37534` row graded 37547 must red"
    # A range token is accepted on any line INSIDE the range it names (the anchor convention).
    inside = (_AUDIT, "`:7559-7562`", _SE, 7562, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)")
    assert not _rows_outside_their_token((inside,)), "a line inside the named range must pass"


def test_a_token_naming_two_addresses_does_not_vouch_for_the_hull_between_them() -> None:
    """Negative control — the fail-open `min..max` left in this arm, re-opened here.

    On the PRE-FIX code `_token_span` returned `(min(numbers), max(numbers))` over EVERY number a
    token carries, so a token naming two unrelated addresses became a range spanning the gap: the
    arm accepted the `58316` neither address names. That is the same false PASS this arm exists to
    refuse — a row asserting an address the document does not carry — so it is refused here rather
    than recorded as a residual. Measured on the shipped tree: 0 of 96 rows carries more than one
    address, so no row's verdict moves.
    """
    between = (_AUDIT, "`:58315`/`:58317`", _SDK, 58316, "rows = proj.g.query(")
    assert _rows_outside_their_token((between,)), (
        "a token naming :58315 and :58317 must NOT vouch for the line between them"
    )
    # …and each address it DOES name is still accepted, so the tightening rejects only the hull.
    for named in (58315, 58317):
        row = (between[0], between[1], between[2], named, between[4])
        assert not _rows_outside_their_token((row,)), (
            f"{named} is an address the token names, so it must pass"
        )


def test_a_row_whose_token_names_no_address_is_reported_not_skipped() -> None:
    """Negative control — an unreadable row must not pass by being unreadable.

    On the PRE-FIX code `_token_span` returned `None` for a token with no digits and the loop
    `continue`d, so such a row was silently exempt from the arm — the gate vouching for a claim it
    never read. Measured on the shipped tree: 0 of 96 rows has a token with no address.
    """
    unaddressed = (_AUDIT, "`_file_human_approval`", _SDK, 37547, "``:Document`` node).")
    assert _rows_outside_their_token((unaddressed,)), (
        "a row whose token names no address must be REPORTED, not skipped"
    )


def test_the_resolve_content_arm_is_unchanged_by_the_token_arm() -> None:
    """The two arms are independent: a row can be content-correct AND token-stale.

    `_resolve` answers two questions — does the token still occur, and does the GRADED line carry the
    fragment — and a row that pins one address while grading another satisfies BOTH (the token exists,
    and the graded line does carry the fragment). That is what the shipped rows demonstrated, and why a
    separate arm was needed instead of a stricter `_resolve`.
    """
    correct_line_wrong_token = (
        _AUDIT, "`:58317`", _SDK, 58315, "rows = proj.g.query(",
    )
    assert _resolve((correct_line_wrong_token,)) == [], (
        "the content arm must stay silent here — that silence is why the token arm exists"
    )
    assert _rows_outside_their_token((correct_line_wrong_token,)), (
        "the token arm must red on the same row the content arm passes"
    )


def test_the_resolver_reds_on_a_double_applied_offset() -> None:
    """Negative control — the exact defect: the loop's cite re-pointed at the `#7704` note below it.

    Reachable: it is the shipped pre-fix state of the m3 brief, reintroduced here by mutating a COPY of
    the shipped row with the cite token LEFT INTACT, so the failure is reported by the CONTENT arm and
    cannot be satisfied by the token arm (which is what a re-pointed token would trip first).

    The spurious line is DERIVED FROM CONTENT, not hardcoded: the note it must land on has moved twice
    since this control was written, and a hardcoded `4907` rotted into a line that no longer holds the
    note — the do-nothing control then failed for the wrong reason (the note check, not the content arm).
    """
    loop_line = 5074
    note_line = next(
        i + 1
        for i, line in enumerate((REPO / _SE).read_text(encoding="utf-8").splitlines())
        if "#7704 (MEASURED MECHANISM" in line
    )
    assert note_line > loop_line, "the `#7704` note must sit BELOW the loop it annotates"
    row = (_M3_BRIEF, "tortoise/search_engine.py:5074", _SE, loop_line, "for rank, (pid, _score) in enumerate(ranked)")
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
    assert docs == {_AUDIT, _SUBSTRATE, _M3_BRIEF, _CENSUS, _EVIDENCE, _INDEX, _PLAN_4997}
    # 48: `#13489`'s review added the `:6989` row (`("Object", ["name"])`, the §2.5 address that
    # had NO pin at all, which is how `:6869` stayed behind its three siblings — `:6984`/`:6985`/`:6990`
    # sit 115/116/121 lines below `:6869`, while `:6989`, the row that had no pin, is the 120th), and row
    # 13b's Source-writer pin (`sdk.py:21769` — `:21648` before `#13486` moved the writer) is the
    # same shape added again, for the same reason.
    # 48 -> 52 at review cycle 3: four rows added for corrections whose sites had NO pin at all
    # (row 13's `entities.py` pair, §2.1's count marker, the Event row's label-derivation cite).
    # 52 -> 55 at review cycle 6: the three addresses review P2-2 measured as pinned NOWHERE —
    # `sdk.py:28085` in `00_index.md` and in `ENTITY-VERDICT-BANDS.md`, and `sdk.py:28449` there too —
    # where a `+1` mutation on all three left 441 tests green. Same shape, same reason.
    # 55 -> 58 at the #13551 fix lane: the three §2.5 sites that cited `sdk.py:37283` (or, in the
    # proximity paragraph, `:37183`) for statements that are on `:37467` / `:37444` at every head the
    # row names — measured above the rows. The pair pins could not see them: a `:37467` anywhere on
    # the line satisfied the count arm, which is the same one-site-vs-one-string gap `:6989` was.
    # 58 -> 59 in the same lane: the FTS row's `entity_type="event"` cite, re-derived BEFORE the
    # `origin/main` integration and therefore 186 lines stale at the merge head (`:57090` -> `:57276`),
    # and 333 by the PRE-`#13833` tip (`:57423`), 527 by this one (`:57617`).
    # 59 -> 61 at the #13793 fix lane: the two `:Document` docstring addresses §5's #12915 move
    # names (`:37144` / `:37186`) had NO pin at all — both shipped one line high (on the `:Source`
    # lines `:37143`/`:37185`) and `git grep -n '37143\|37144' -- tests docs` reached only the prose
    # sites, so no mutant reddened anything. Same shape as the `:6989` and `sdk.py:28085` rows above.
    # The arm is an anti-SHRINK arm, so it moves with a documented ADDITION, never with a deletion.
    # 61 -> 62 at the #13854 fix lane: the vector-index-parity plan's reader row was re-pointed to
    # `hosted_backup.py:1548` (a comment) for `_index_has_is_operator()`, which was `:2666` at that lane's head
    # — and that document had NO pin at all, so nothing would have reddened the next rotation of it.
    # 62 -> 63 after #13879's merge: §2.5's `:Tag` row cites `mcp_server.py:11281` (that head's
    # spelling — `#14169` has since moved the live address to `:11590`), re-derived from the
    # pin-head `10730`, and NOTHING read it — the same class as the `:6989` and `hosted_backup.py:2683`
    # rows above.
    # 63 -> 64 at the #13992 review (P2): the SAME address is written TWICE in the document, and the
    # row above pins only the §2.5 spelling (`mcp_server.py:11590` now, `:11281` then) — the header
    # enumerated it BARE as
    # `:11281` THEN, where no row and no count reached; it enumerates the live `:11590` now, which the
    # bare-token row above does reach. Reverting just the header half left every arm green, measured
    # above. One ROW per SITE, never one row per STRING: `:8515` (then `:8206`) sits on that same
    # header line and is the neighbour that already carries its own bare-token row — ⛔ `:5720` is NOT
    # on that line: its bare sites are the `:5529` passthrough parenthetical and §2.5's table row,
    # which the `:5720` row covers.
    # 68 -> 72 at the #14043 review (finding 3): four addresses the revision writes at THIS head were
    # in neither the header's enumeration nor `_PINS`, so the header's reader rule sent a reader to the
    # pin, where none of them opens. Same shape as the `:6989` / `hosted_backup.py:2683` rows above:
    # one ROW per SITE, and an unlisted token is coverage this file only appeared to have.
    # 72 -> 77 at the SAME review's finding 2: the class was still unclosed for FIVE addresses this
    # revision writes — the new §2.2 `:Source` reader row's `session_source_prose_migration.py`
    # cites (`:218-223`, `:1263-1268`, `:274-281`) and row 13's inner pair (`:277-279`) plus its
    # `_CONTROL_LEXICON_ROWS` read (`:1292-1300`). ⛔ Those five are PIN-STABLE, not head-relative: one
    # blob at both heads, so the header's default resolves them right and the defect was purely the
    # missing guard — the document was the only thing naming them, so a merge inserting into that file
    # moved them with nothing red. That is the failure mode the four rows above exist to stop, and
    # §2.7's own note (file-scoped drift is "either fixed everywhere or trusted nowhere") is why the
    # file's remaining unpinned sites are closed in the same commit.
    # 77 -> 80 by the rc lane's §2.6 re-cut (`0a53ad23d8`): the three statements it re-pointed at this
    # head — `hosted_backup.py:892`, `:1316`, `:1517` — needed rows of their own, because a re-point
    # whose new address is unpinned "moves the defect rather than closing it" (that re-cut's own
    # re-review). MEASURED, not inferred: the count of `_PINS` rows at each of these heads is
    # 72 (ecb137107e) -> 77 (298d3328db, the five above) -> 80 (0a53ad23d8).
    # 80 -> 82 at the #14043 review (finding 1, this cycle): §6.3's `sdk.py:16077-16082` and
    # `sdk.py:3801` were in neither list and unpinned, and the pin default opens on prose and on an
    # empty line respectively. (The span landed as `:16077-16081` in #13994, which re-derived this
    # document on top of its own `:Point` join; the END stayed one short until #14087 re-derived it
    # here. One ROW, one token — the row count did not move.)
    # 82 -> 83 at the #14087 review round 7 (P3): `00_index.md`'s entity-verdict row carries a
    # SECOND present-tense this-head address on the line this PR edits (`entity_verdict.py:43`),
    # and NOTHING read it — its `sdk.py:28581` sibling was pinned and the neighbour was not, so a
    # rotation of `entity_verdict.py` under that cite would have moved it silently. Same shape as
    # the `:6989` / `sdk.py:28085` / `hosted_backup.py:2683` rows above.
    assert len(_PINS) == 83


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
    count(*)`, not the `run_structural_query` scan the table row quotes at `:4878-4880`. Measured on
    `origin/main` at that re-cut: the table row named `:4708-4710`, the verdict paragraph named
    `:4489-4490` — two addresses for one reader, in one table, again. The `:4350-4351` assertion cannot see
    it (`origin/main` reads 0 there), so this test would have passed over the live defect; the
    `:4489-4490` assertion is what makes it reachable.

    ⛔ **AND IT RETURNED A THIRD TIME, WHICH IS WHY THE LAST ARM AGREES THE TWO SPELLINGS INSTEAD OF
    ASSERTING ONE LITERAL — the measured witness that a literal cannot see an agreement.** `#13466`
    re-derived §2.1's TABLE row to `:4878-4880` and left this verdict paragraph at the pre-move
    `:4708-4710`, so `main` again gave TWO addresses for one reader (`:4723` is
    `where_clause = " AND ".join(conditions)`, the statement BEFORE the scan; `:4708-4710` there is the
    comment block that justifies the kind clause). This test's first
    assertion — a literal bound to the verdict's own number — stayed GREEN over it, because a pin that
    names one side of a two-sided claim has nothing to disagree with. The pair is now read out of the
    document and compared; the literal is kept only as the content-verified value at this head.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    assert "`none`: `search_engine.py:4878-4880`, a plain label scan" in text, (
        "§2.1's verdict paragraph no longer names the label scan at `:4878-4880`; re-derive it from "
        "the statement rather than applying a delta to the address beside it."
    )
    assert "`none`: `search_engine.py:4350-4351`" not in text, (
        "§2.1's verdict paragraph names the label scan at `:4350-4351` while the table above it names "
        "the same reader row at `:4878-4880` — two addresses for one reader, and only one of them can "
        "open at any head. That is the defect class this file exists for."
    )
    assert "`none`: `search_engine.py:4489-4490`" not in text, (
        "§2.1's verdict paragraph names `:4489-4490`, which is `run_vector_query`'s "
        "`MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(*)` — a different plain label "
        "scan from the `run_structural_query` one the table row quotes at `:4878-4880`. `#13290` reached "
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
    open at `main` and are named in neither list — the set `_MAIN_SET_MISSED` pins, every member of which
    the CLAIM arm below requires the header to name. ⚠️ That list is deliberately NOT restated here as
    literals: measured at three editions, `:2464` and `:2521` appear in the enumeration of none of them
    (`295c26ebc8`, `origin/main`, HEAD) and `:2755` only on `origin/main`, so a hardcoded enumeration in
    this docstring would be a claim about history that no edition supports — the addresses travel in the
    pinned tuple, which is the artifact that can be re-derived. `:3778` is a
    `MERGE (n:…) SET n += $props` at the pin and the `cache_tier` arm at `main`, so a reader who followed
    the rule opened the pin and landed on a statement their row does not quote: "cite rotated, reader
    confidently opens the wrong line" — this document's own failure class, inverted.

    Two arms, because the defect was two-sided:

    * CONTENT — each of the eight carries, **on this branch's tree** (the tree this test reads),
      the fragment the document quotes for it. `_PINS` reaches five of them; `:2485`, `:2542` and
      `projection/entities.py:5765` were in NO pin before this one. ⛔ The earlier form of this
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
    discriminating: that same pre-fix file carries **18** occurrences of `cache_projection` AFTER `## 1.`
    (row 13, §2.5), so an unbounded `in text` check would pass on the broken file — it is the TOKEN, not
    any one address, that the unbounded form cannot distinguish.
    """
    # ⛔ THE POPULATION HAS AN ANTI-SHRINK ARM (#13509), the mirror of the one the sibling
    # `_MAIN_HEAD_FIGURES` guard carries. BOTH use sites below ITERATE this tuple, so a row deleted to
    # silence a re-point red simply stops existing in both: its content pin and the "the header names
    # it" assertion vanish together and nothing reds — the false PASS the sibling arm was added for,
    # one table over. EIGHT is the group the `#13124` review measured; the other six are Round 8's, and a
    # fifteenth is a
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

    `#13489`'s first cut re-pointed the Object entry from `:6869` to `:6938` — the value THAT cut's own
    head carries, `:6869` + 69 IN `projection/__init__.py` (an address delta, not a document distance);
    this tree's value for §2.5's row has since moved on from it, and re-pointing the header to the
    current one is WRONG under a sentence that announces the other head. A
    `⚠️ CORRECTED` marker records that the text changed, never that the change was right: the
    FALSE CORRECTION this file's own docstring is built around, one head over.

    NON-VACUOUS on the pre-fix document (`git show 26a68370b327:docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md`):
    the header carries `:6938` after the fragment and `:6869` nowhere, so this assertion reds there. The
    measurement that settles which number belongs to the announced head is
    `git show ae54df166d:tortoise/projection/__init__.py | sed -n '6869p;6938p'` ->
    `("Object", ["name"]),` and then `# background RDB/AOF child in the fork slot, and a hung un-reaped`.

    ⚠️ The assertion is on the `:6869` ADJACENCY, not on `:6989`'s absence — but it does NOT follow
    that disclosure is free, and an earlier form of this docstring said it was. The declaration must sit
    DIRECTLY above `` :6869`; ``; anything between them breaks the adjacency this arm reads. And
    disclosing this tree's figure appends a SECOND `:6989` occurrence, and
    `test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence` already declares 2 — which IS
    the count this document carries, so that disclosure is in place. A further one is a TWO-file
    edit: keep the declaration above `` :6869`; ``, put it AFTER the semicolon, and bump
    `_TOKEN_OCCURRENCES[':6989']` in the same change. ⛔ Both arms are correct — do not clear a red by loosening either one; that
    deletes the only protection on the entry that actually suffered the false correction. The document's
    own shape puts the DECLARED head's figure FIRST with this tree's after it in parens (`:33583`
    there, `:33637` at this head), not the reverse.
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
        "`capture_redactions` writer `:2876` (on this tree `:2921`)",
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
        "at the `main` head that paragraph announces (`ae54df166d`). `:6989` is THIS tree's value (and "
        "§2.5's row); putting it here re-points a history cite to a working-tree address, the "
        "`⚠️ CORRECTED` failure this file exists to stop. Restore `:6869` directly above a `;`. To ALSO "
        "disclose this tree's figure, keep the declaration where it is, append the disclosure AFTER the "
        "semicolon, and bump `_TOKEN_OCCURRENCES[':6989']` (currently 2) in the same change — otherwise "
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
#: the header, and the addresses that REPLACED them (`:6974-6990`, `:37444`/`:37467`, `:24048`,
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
    row to ONE markdown LINE, and D5 is one line carrying 262 numbers — so any `:12111`
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


#: ⛔ `#13854` cite families — the `#13854` review measured 27 addresses DELTA-SHIFTED (the branch's own
#: insert delta applied to a cite whose row survives two different offsets), so they opened on a comment
#: or unrelated code at BOTH heads. The `faaad5308c` predecessor then applied a blanket `-23` to eight
#: `projection/entities.py` cites that were already correct at that head — the FALSE CORRECTION this whole
#: file exists for — and re-derived nothing: `:1223` was a blank line, `:3857` a module import.
#:
#: Every row below was re-taken by CONTENT on THIS tree (`grep -n '<the fragment the row quotes>' <path>`),
#: never by arithmetic, and each is a claim about the line the DOCUMENT currently names. NON-VACUOUS on the
#: pre-fix document: `sdk.py:38895` was `last_pass is None…` prose at this head (the statement is `:42105`),
#: `sdk.py:52972` was a comment (statement `:58561`), and `entities.py:1223` was EMPTY (the declaration is
#: `:1246`) — so the fragment arm reds on every one of them.
#:
#: ⚠️ A RANGE row anchors on the line INSIDE the cited range that carries the quoted fragment (the same
#: convention `_PINS` states), and `test_..._range_anchors_are_inside_their_range` below proves the anchor is
#: in the range the token names — without it a range row could pin a line the range does not cover.
_REDEFINED_13854: tuple[tuple[str, str, int, str, int], ...] = (
    ("sdk.py:18035", "tortoise/sdk.py", 18035, "\"MATCH (p:Point {id:$id})-[:extractedFrom]->\"", 1),
    ("sdk.py:41728", "tortoise/sdk.py", 41728, "\"MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NUL", 3),
    (":42486", "tortoise/sdk.py", 42486, "\"MATCH (s:Source)-[:references]->(o:Source) RETURN s.url, o.url\",", 0),
    ("sdk.py:58966", "tortoise/sdk.py", 58966, "\"MATCH (s:Source {url:$url})-[:references]->(n) RETURN count(n)\",", 2),
    ("sdk.py:20141-20144", "tortoise/sdk.py", 20144, "\"AND coalesce(n.is_episodic, false) <> true \")", 1),
    ("sdk.py:20149", "tortoise/sdk.py", 20149, "_stamp_capture_provenance(", 1),
    ("sdk.py:58315-58339", "tortoise/sdk.py", 58317, "\"s.turn_count, s.actor_user_id, s.harness, \"", 1),
    ("sdk.py:58317-58339", "tortoise/sdk.py", 58317, "\"s.turn_count, s.actor_user_id, s.harness, \"", 6),
    ("sdk.py:58317", "tortoise/sdk.py", 58317, "\"s.turn_count, s.actor_user_id, s.harness, \"", 8),
    ("sdk.py:4265-4268", "tortoise/sdk.py", 4265, "\"s.client_captured_at_source=CASE WHEN s.client_captured_at IS NULL \"", 3),
    ("sdk.py:4399-4406", "tortoise/sdk.py", 4404, "\"RETURN s.turn_max_index, s.turn_count, collect(t.id)\",", 2),
    ("sdk.py:2898", "tortoise/sdk.py", 2898, "\"SET s.capture_redactions=$redactions \"", 1),
    ("sdk.py:31533", "tortoise/sdk.py", 31533, "\"MATCH (t:Tag) \"", 1),
    ("sdk.py:31551", "tortoise/sdk.py", 31551, "\"MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) \"", 1),
    ("sdk.py:24513", "tortoise/sdk.py", 24513, "\"MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) \"", 1),
    ("sdk.py:24857", "tortoise/sdk.py", 24857, "\"MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t\"),", 1),
    ("sdk.py:27800", "tortoise/sdk.py", 27800, "\"MATCH (n:Point {id:$id}) RETURN properties(n)\",", 2),
    (":28699", "tortoise/sdk.py", 28699, "\"MATCH (n:Point {id:$id}) RETURN properties(n)\",", 0),
    (":30357", "tortoise/sdk.py", 30357, "f\"MATCH (n:Point) WHERE {where} RETURN properties(n)\",", 0),
    (":30453", "tortoise/sdk.py", 30453, "f\"MATCH (n:Point) WHERE {where} RETURN properties(n)\"", 0),
    (":30552", "tortoise/sdk.py", 30552, "\"MATCH (n:Point {id:$id}) RETURN properties(n)\",", 0),
    (":44181", "tortoise/sdk.py", 44181, "\"RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim\",", 0),
    ("entities.py:1292", "tortoise/projection/entities.py", 1292, "_POINT_JSON_RECORD_PROPS: frozenset = frozenset({\"mentions\"})", 2),
    ("entities.py:2068-2076", "tortoise/projection/entities.py", 2068, "# once. MEASURED at #10389's review head, before this entry: one SDK", 1),
    ("entities.py:2506", "tortoise/projection/entities.py", 2506, "\"operator\", \"provenance\", \"about_entities\", \"aboutEntities\",", 1),
    ("entities.py:2555", "tortoise/projection/entities.py", 2555, "_POINT_DENY: frozenset = frozenset({", 1),
    ("entities.py:2415-2416", "tortoise/projection/entities.py", 2416, "# out first, because the wipe destroys the only copy (\u00a717.2, `#11325`)", 1),
    ("entities.py:2419-2420", "tortoise/projection/entities.py", 2420, "# `update_point(<id>, story_arc=None)` \u2014 #11325, and only on the rows ", 1),
    ("entities.py:3177", "tortoise/projection/entities.py", 3177, "set_clauses.append(\"n.sourceVersionTransit=$sv\")", 1),
    ("entities.py:3982", "tortoise/projection/entities.py", 3982, "\"MERGE (s:Session {id:$sid}) SET s.is_episodic=true\",", 1),
    (":3295-3297", "tortoise/projection/entities.py", 3297, "self._POINT_HANDLED | self._POINT_DENY,", 1),
    (":3339", "tortoise/projection/entities.py", 3339, "for key in self._POINT_DENY - self._POINT_HANDLED:", 1),
    (":3389", "tortoise/projection/entities.py", 3389, "or key in self._POINT_DENY or key in self._META_KEYS:", 1),
)


def test_the_13854_cite_families_open_on_the_line_they_quote() -> None:
    """Every re-derived cite in the `#13854` families still resolves, at the head it names.

    The token arm catches a re-point (the document no longer carries the address) and the occurrence
    count makes every SITE load-bearing; the fragment arm catches the number staying while the line
    under it moves.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    for token, path, line, fragment, expected in _REDEFINED_13854:
        assert text.count(token) == expected, (
            f"{_AUDIT} carries {token!r} {text.count(token)}x, not {expected}x — a SITE was re-pointed "
            "while a sibling kept the string. Re-derive by CONTENT and update the count."
        )
        target = (REPO / path).read_text(encoding="utf-8").splitlines()
        assert 0 < line <= len(target), f"{path} has no line {line}"
        assert fragment in target[line - 1], (
            f"{path}:{line} no longer carries {fragment!r} — it reads {target[line - 1].strip()[:96]!r}. "
            "The cite is bound to a line, so re-derive it from the row's own quoted fragment instead of "
            "moving it by a delta."
        )


def test_the_13854_range_anchors_are_inside_the_range_the_token_names() -> None:
    """A range row's anchor must be a line the range actually covers."""
    for token, path, line, _fragment, _expected in _REDEFINED_13854:
        found = re.search(r":(\d+)-(\d+)$", token)
        if not found:
            continue
        start, end = int(found.group(1)), int(found.group(2))
        assert start <= line <= end, (
            f"{path}:{line} is the anchor pinned for {token!r}, which covers {start}-{end} — a range row "
            "whose anchor sits outside its own range resolves nothing the row quotes."
        )
