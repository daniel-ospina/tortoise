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

import functools
import re
import subprocess
import sys
import warnings
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
_LIVE = "tortoise/live.py"
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
    (_AUDIT, "search_engine.py:7193", _SE, 7193, "(op)-[r2:IMPL|hasPart]-(other:Point)"),
    (_AUDIT, "search_engine.py:7600", _SE, 7600, "(n:Point)-[r:aboutSubject]->(:Subject)"),
    (_AUDIT, "search_engine.py:7602", _SE, 7602, "(m:Event)-[r2:aboutSubject]->(:Subject)"),
    # Row 149's TWO BARE range cites carry no `search_engine.py` prefix, so the token-level pins for
    # `6659`/`6661` above do not reach them — they are the only shifted mentions in this document the
    # first pin set missed. Both were already correct at the pre-fix head, so these two are
    # COMPLETENESS pins: they catch the re-point/double-shift that DID happen in this class (`:4549`
    # re-pointed to `:4552`), which is the failure the whole file exists for.
    (_AUDIT, "`:7599-7602`", _SE, 7599, "_SUBJECT_SOURCE_SCOPED_PROBE = ("),
    (_AUDIT, "`:7605-7644`", _SE, 7605, "def subject_binding_available"),
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
    # `_MAIN_SET_MISSED` below pins the group the header was missing — the `#13124` eight plus Round 8's
    # six, see the anti-shrink arm; these are the entries the header
    # already named. Without them the enumeration is a promise: `#13290` moved every `search_engine.py`
    # entry in it and NOTHING red — the document went on naming held addresses over a tree that had
    # moved them, and the reader rule sent the reader to the wrong line. A pin per entry is what turns
    # the list into a guard.
    (_AUDIT, "`:2921`", _SDK, 2921, "MATCH (s:Session {id:$sid}) SET s.capture_redactions=$redactions"),
    (_AUDIT, "`sdk.py:37883`", _SDK, 37883, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
    (_AUDIT, "`:37906`", _SDK, 37906, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
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
    (_AUDIT, "| `tortoise/sdk.py:37906` |", _SDK, 37906, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "the `-[r]->` walk (`sdk.py:37906`)", _SDK, 37906, "MATCH (s:Subject {id:$sid})-[r]->(n)"),
    (_AUDIT, "they are `:37883` and", _SDK, 37883, "MATCH (s:Subject {id:$sid}) RETURN properties(s)"),
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
    # so the header counts it as drifted rather than compared, and it is ONE OF THIRTEEN that are
    # incomparable at the pin — measured at this head, and stated by the header itself: 221 comparable,
    # 128 of them drifted, plus EIGHT of those thirteen = 136 of the 234. ⛔ THIS SENTENCE HAS NOW SHIPPED
    # WRONG TWICE, both times with figures that were right about an EARLIER edition: `211 comparable, 119
    # of them drifted, plus those three = 122 of the 214` (the pre-`#14246` corpus), then `223 / 130 /
    # eight of eleven / 138` (the `#14246` corpus — still what this branch inherited, and no longer true
    # of the document AFTER its own re-point moved two pairs past the pin's EOF and two more past
    # `main`'s). Neither edition was graded: `_SPLIT_*` reads the DOCUMENT, and a comment beside a pin is
    # prose to every arm in this file. The lesson this repetition states is the file's own: a figure
    # quoted from a header rots the moment the header moves, so quote it at the level of "stated by the
    # header" and let `_PIN_FILE_LENGTHS` grade the header instead of transcribing it here. ⛔ The
    # earlier form of this comment said "the seam above it is line-neutral, so nothing moved" and
    # carried `57276`: the seam wrap sits above the cite, and the statement has moved +341 since
    # the head this pin was written at (+147 to the pre-`#13833` tip, then `main`'s `#13833` a
    # further 194), so the token moves WITH the line — a pin whose token and
    # target disagree cannot red on the stale cite it exists to catch (#13750 VGATE).
    (_AUDIT, '`sdk.py:58099` passes `"event"`', _SDK, 58099, 'entity_type="event"'),
    (_AUDIT, "`audit.py:817`", _AU, 817, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2452`", _AU, 2452, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`:2463`", _AU, 2463, "OPTIONAL MATCH (carrier:Object) WHERE carrier.name"),
    (_AUDIT, "`mcp_server.py:5250`", _MCP, 5250, "entity_type=entity_type"),
    # ⛔ §2.5's `:Tag` traversal row cites `mcp_server.py:11590` and, until this row, NOTHING read
    # it (`grep -rn 11590 tests/` -> nothing): the audit document's header promises a reader for
    # every entry it re-derives, and this one had none. `:11280` is the `g.query(` call; the
    # document cites the query LITERAL's first line, which is `:11590` (its siblings in the same
    # paragraph follow the same convention — `sdk.py:31533` is the literal while `:31501` is the call).
    (_AUDIT, "`mcp_server.py:11590`", _MCP, 11590, "OPTIONAL MATCH (t)<-[:TAGGED]-(q)"),
    # ⛔ The SAME address has a SECOND written site — the header enumeration spells it BARE
    # (`` `mcp_server.py:5250`/`:8515`/`:11590` ``), and the §2.5 token above cannot see a bare
    # `:NNNN`. Review at `b9e7822362c6`, P2: reverting ONLY the header half (`:11590` -> `:11280`)
    # left every arm green — `_PINS` counts were unchanged (63/63), `_TOKEN_OCCURRENCES`' key count
    # stayed 1 (the §2.5 spelling is a different key), `_REDEFINED_13854` was untouched and the
    # `_resolved_pairs` census was identical (223 pairs) — so the document could name two addresses
    # for one reader with nothing red. That is the false pass this file exists to stop, and it is the
    # `#13466` shape (four header entries re-pointed, nothing red). Its siblings on the same header
    # line are pinned per SITE for exactly this reason: `` `:8515` `` and `` `:5720` `` each carry a
    # bare-token row with count 2 (header + §2.5). This row and its count are the site the re-derived
    # §2.5 row left behind.
    (_AUDIT, "`:11590`", _MCP, 11590, "OPTIONAL MATCH (t)<-[:TAGGED]-(q)"),
    # 1 -> 4 at the #13998 head: the branch's OWN prose adds three more BARE sites of this same
    # address (the #13991 merge added two; the non-comparable enumeration in the drift header is the
    # third, added when `5bb3b32518` re-pointed this address and moved the pair past the pin's
    # `mcp_server.py`), so the bare-token row DIRECTLY ABOVE (`` `:11590` ``, count 1 -> 4) can no
    # longer see every spelling of it; the `mcp_server.py:11590` row above it (count 1, unchanged —
    # the row whose token carries the path, not a bare `:NNNN`) covers only the §2.5 one-token
    # spelling. Deliberately no "N lines above": that distance moved twice on this branch already,
    # and a stated count is the defect class this file exists to catch. Measured on the merged document
    # (`.venv/bin/python -c 'from pathlib import Path; t=Path("docs/architecture/
    # CAPABILITY-PLACEMENT-AUDIT.md").read_text(); print(t.count("`:11590`"))'` -> 4), with the site
    # lines RE-MEASURED on this document rather than carried — `grep -n '`:11590`' on the MERGED
    # document gives `:139` / `:203` / `:222` / `:647`. They have moved repeatedly: `main`'s merge
    # `0656b056d7` shifted two of them, the P2 fix above added +2 to a third, the two-line "five pairs
    # as two code spans" correction (#13991 review P2-A) shifted every one of them by +2, and this
    # PR's own edits above them shifted all four again — which is why this block states the
    # MEASUREMENT and not a delta:
    #   :142  the drift header's non-comparable enumeration, which names this address among the twelve
    #         that resolve past the pin's copy of their file (added by #13998; see the count above);
    #   :206  the header trio above — main's site, the one this row was created for;
    #   :225  the §2.5 `:Tag`-family enumeration this branch adds, which lists all five reader
    #         addresses (the four `sdk.py` ones plus this `mcp_server.py` one) as BARE siblings so the
    #         `_REDEFINED_13854` one-token counts are not doubled;
    #   :650  the same branch's §2.5 provenance sentence, `:11280` -> `:11590`.
    # Re-derive with `grep -n '`:11590`' docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md` before
    # trusting this list on a later head: a line number in prose is exactly the claim this file pins
    # for the document, and it drifts the same way.
    # All four name ONE address with ONE meaning, so this is not the `b9e7822362c6` class (two
    # addresses for one reader). The count is kept at the measured number rather than deleting a
    # spelling: re-pointing any one of the four now reds here, which is the discrimination the row
    # exists for.
    (_AUDIT, "`:5720`", _MCP, 5720, "entity_type=entity_type"),
    (_AUDIT, "`:8515`", _MCP, 8515, "entity_type=entity_type"),
    # ⛔ The four `sdk.py` members of §2.5's `:Tag` row are enumerated in the header as BARE `:NNNN`
    # siblings — a spelling none of the rows above reaches (they all require a path or the one-token
    # form), which is the same one-site-vs-one-string gap the `:11590` header half and `:6989` were:
    # re-pointing any one of them to a stale number left every arm green. One ROW per SITE, pinned by
    # the fragment the header paragraph quotes for it.
    (_AUDIT, "`:31551`", _SDK, 31551, 'MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag})'),
    (_AUDIT, "`:31533`", _SDK, 31533, '"MATCH (t:Tag) "'),
    (_AUDIT, "`:24513`", _SDK, 24513, "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) "),
    (_AUDIT, "`:24857`", _SDK, 24857, 'MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t'),
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
    (_AUDIT, "sdk.py:24626", _SDK, 24626, "(n:Point {id:$id})-[:TAGGED]->()"),
    # ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): `search_engine.py` is NOT touched
    # by this PR (`git diff --stat origin/main...HEAD -- tortoise/search_engine.py` prints nothing), so
    # `main`'s `#14278` re-point is the measured one at the merged tree: `:7226` is
    # `"RETURN op.id, type(r2) AS et, count(other)"` and this branch's pre-merge `:7201` now reads
    # `# #2490: aligned has_ep gate …` (a COMMENT, measured with `sed -n '7201p'` on the resolved tree).
    (_AUDIT, "search_engine.py:7226", _SE, 7226, "RETURN op.id, type(r2) AS et, count(other)"),
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
    # ⛔ The same sentence's OPENING values were pinned by nothing, and a merge resurrected the +21-era
    # siblings over main's corrected ones (`(opening :37548` = a kwarg line, `(opening :37588` = the other
    # docstring) while the two graded headlines stayed right — green pins over a wrong sentence (#14038
    # review, cycle 2). Pinned as the `def` lines they claim to be, and the `:Source` line between them
    # (`:37546` carries `D10: a document is a ``:Source``, not a` — the line ABOVE `:37547`).
    (_SUBSTRATE, "(opening `:37527`", _SDK, 37527, "def record_decision"),
    (_SUBSTRATE, "`:37546` is the `:Source` line", _SDK, 37546, "D10: a document is a ``:Source``, not a"),
    (_SUBSTRATE, "(opening `:37567`)**)", _SDK, 37567, "def _file_human_approval"),
    # ⛔ And the capability-placement heal pair: the two `hosted_api.py` addresses were re-pointed by
    # DELTA in the merge (+114) onto the operator-stamp statement and a comment line, while the fragment
    # the row quotes (`coalesce(n.is_episodic,false) <> true`) lives 37 lines lower. Pinned by content —
    # §2.4's prose AND row 2b's own copy, which is why each token occurs twice.
    (_AUDIT, "hosted_api.py:21451-21453", "tortoise/hosted_api.py", 21453, "coalesce(n.is_episodic,false) <> true"),
    (_AUDIT, "hosted_api.py:21458", "tortoise/hosted_api.py", 21458, "_stamp_capture_provenance("),
    # ⛔ The four addresses the `#14038` cycle-3 pass RE-DERIVED BY CONTENT after the −21 fold had left
    # them pointing at a comment and a receipt builder — and, pinned here, the reason they cannot drift
    # back silently: `:21128` was a comment (the calls are `21433`/`43794`/`43902`), `hosted_api.py`'s
    # `:22529` was inside a receipt builder (the call is `:23062`), and `sdk.py:18872` was a blank `#`
    # with `:18769` a comment (the extractor PAYLOAD read is `:21393`, `pt` bound at `:21321`).
    # (`:61999`/`:62019` are re-pointed and pinned by the row-2d pass above; not duplicated here.)
    (_AUDIT, "hosted_api.py:23062", "tortoise/hosted_api.py", 23062, "sdk._find_point_by_content("),
    (_AUDIT, "sdk.py:21433", _SDK, 21433, "hit_id = self._find_point_by_content("),
    (_AUDIT, "sdk.py:21393", _SDK, 21393, '_mention_records = pt.get("mentions")'),
    (_AUDIT, "`:21321`", _SDK, 21321, 'for pt in payload.get("points", []) or []:'),
    # ⛔ ROW 2d's OWN pass (#14038, cycle 6). Five of the nine addresses this row carried were stale at
    # `28ba95d64d` — measured line by line, `:32395` was the comment `# confidence/weight (they are plain
    # direct-edge attributes).`, `:43479` was `_find_point_by_content`'s docstring prose, `:43416` was
    # `def _fold_source_evidence(`, `:32527-32528` was `for key in ("confidence", "weight"):`, and
    # `:32566` was `# Key the result so a lookup by the raw endpoint value finds the` — so the row quoted
    # `MATCH (n:Point {content_hash:$ch})` and `RETURN n.content_hash, …` at addresses holding none of it.
    # Re-pointed BY CONTENT, never by delta, and pinned so the same rot cannot come back silently: the
    # population arms are all blind here (`_resolved_pairs` counts only `path.py:NNNN` and two-code-span
    # spellings, so these bare `:NNNN` continuations move the 345/227/215/167 figures by nothing —
    # measured identical before and after the edit — and a line number is only graded by opening it).
    # ⛔ `sdk.py:32759` was CORRECT all along: a hand-off table on this PR told lanes to move it to
    # `:32776`, which is the sibling `any_hash` map INSIDE the same function, not the first map. Pinned
    # so the false-correction shape that table took cannot take it again.
    (_AUDIT, "sdk.py:32759", _SDK, 32759, '"MATCH (n:Point {content_hash:$ch}) WHERE "'),
    (_AUDIT, "`:32776`", _SDK, 32776, '"MATCH (n:Point {content_hash:$ch}) WHERE "'),
    (_AUDIT, "`:43860`", _SDK, 43860, 'f"MATCH (n:Point {{content_hash:$ch}}) WHERE "'),
    (_AUDIT, "`:43797`", _SDK, 43797, "def _find_point_by_content("),
    (_AUDIT, "`:32908-32909`", _SDK, 32909, '"RETURN n.content_hash, n.pointKind, n.content, n.id, n.key, "'),
    (_AUDIT, "`:32947`", _SDK, 32947, '"RETURN n.content_hash, n.pointKind, n.content, n.id, n.key, "'),
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
    (_AUDIT, 'sdk.py:22225', _SDK, 22225, 'search_text = _source_search_text(topics=topics)'),
    (_AUDIT, 'sdk.py:22267', 'tortoise/sdk.py', 22267, 's.summary=CASE WHEN $sum'),
    (_AUDIT, '`:22390`', 'tortoise/sdk.py', 22390, 's.summary, s.topics, s.'),
    (_AUDIT, 'hosted_api.py:23615', 'tortoise/hosted_api.py', 23615, 'MERGE (s:Source {url:$did})'),
    (_AUDIT, '`:23427`', 'tortoise/hosted_api.py', 23427, 'def _execute_commit_writes'),
    (_AUDIT, '`:34390`', 'tortoise/hosted_api.py', 34390, 'MERGE (p:PackInstall {namespace: $ns})'),
    # ⚠️ Three sites on this row that had NO pin at all, which is why they rotted green: the
    # `:CommitRecord` reader pair and the standalone `:Session` MERGE sat outside every `_PINS` /
    # occurrence entry, so a stale number there produced no red. Re-derived at `8f7724b593c` by
    # content (`grep -n` for each statement), not by applying the +108 offset.
    (_AUDIT, 'hosted_api.py:22969-22970', 'tortoise/hosted_api.py', 22970, 'SET r.telemetry=$t, r.budget_warn=$warn'),
    (_AUDIT, '`:25169-25170`', 'tortoise/hosted_api.py', 25170, 'SET r.write_ops_billed=1'),
    (_AUDIT, 'hosted_api.py:25071-25075', 'tortoise/hosted_api.py', 25073, 's.value_nodes_held = coalesce(s.value_nodes_held, 0) + $n'),
    # ⚠️ Three addresses on the same rows that were 143-152 lines off and pinned NOWHERE — the mixed
    # state a reader cannot tell apart: one exact, freshly pinned address beside three false ones.
    # Re-derived at `69d79287f5f` by opening each line, not by offset.
    (_AUDIT, 'hosted_api.py:22840-22841', 'tortoise/hosted_api.py', 22841, 'coalesce(s.value_nodes_created, 0), coalesce(s.value_nodes_held, 0)'),
    (_AUDIT, 'hosted_api.py:23549', 'tortoise/hosted_api.py', 23549, 's.value_nodes_created = coalesce(s.value_nodes_created, 0) +'),
    (_AUDIT, '`:23549`', 'tortoise/hosted_api.py', 23549, 's.value_nodes_created = coalesce(s.value_nodes_created, 0) +'),
    (_AUDIT, 'hosted_api.py:23551-23553', 'tortoise/hosted_api.py', 23553, 's.commit_count = coalesce(s.commit_count, 0) +'),
    (_AUDIT, '`:23551-23553`', 'tortoise/hosted_api.py', 23553, 's.commit_count = coalesce(s.commit_count, 0) +'),
    # the same statement written BARE on the label-scan row — a different SITE, so its own row
    (_AUDIT, '`:25071-25075`', 'tortoise/hosted_api.py', 25073, 's.value_nodes_held = coalesce(s.value_nodes_held, 0) + $n'),
    # ⚠️ The LAST TWO sites of the same rot, left "reported, not touched" by the `8f7724b593c` review
    # and unpinned here, which is exactly why they were still stale at `69d79287f5f`: the composed
    # `turn_max_index` site (§2.4 ×2, §6.1, row 7) and the LIST endpoint's `actor_user_id` condition
    # (§7 ×2, §2.4) were in NO row at all. Both re-derived at `69d79287f5f` by OPENING the statement,
    # not by applying the delta — verified against `ae26adf963`, where the same cites resolved:
    # `45028` was `prior = proj.g.query(` and `50935` was `conds.insert(0, "s.actor_user_id = $uid")`.
    # ⚠️ Re-derived by CONTENT again at `b377f6f6`: the merge that folded `origin/main` added 13 lines
    # to `hosted_api.py` above both anchors, so the composed `turn_max_index` statement now opens at
    # `45713` (`prior = proj.g.query(`) and its mark composition closes at `45725`
    # (`total = max(total, mark + 1)`), while row 7's wider range ends on `45730` (`return total`).
    # The LIST endpoint's `actor_user_id` condition moved `51607` -> `51620`. MEASURED:
    # `grep -n 'RETURN s.turn_max_index, s.turn_count, count(t)' tortoise/hosted_api.py` -> 45717 and
    # `grep -n 's.actor_user_id = $uid' tortoise/hosted_api.py` -> 51620 — never an applied delta.
    (_AUDIT, 'hosted_api.py:45713-45725', 'tortoise/hosted_api.py', 45717, 'RETURN s.turn_max_index, s.turn_count, count(t)'),
    (_AUDIT, 'hosted_api.py:45713-45730', 'tortoise/hosted_api.py', 45717, 'RETURN s.turn_max_index, s.turn_count, count(t)'),
    (_AUDIT, 'hosted_api.py:51620', 'tortoise/hosted_api.py', 51620, 's.actor_user_id = $uid'),
    # the same condition written BARE twice in the header's comparability clause — its own SITE
    (_AUDIT, '`:51620`', 'tortoise/hosted_api.py', 51620, 's.actor_user_id = $uid'),
    # --- the four corrections this pass made that NO row reached (review cycle 3, F5). Each token
    # carries its own wording, not a bare number, because a bare `:NNNN` is satisfied by any sibling
    # spelling of the same digits — the defect `test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence`
    # exists for, and the reason row 13's parenthetical and §2.1's count marker survived three passes.
    (_AUDIT, '`:5434` at this head', 'tortoise/projection/entities.py', 5434, 'def _upsert_document(self, ev: dict) -> None:'),
    (_AUDIT, '`:7240` at this head', 'tortoise/projection/entities.py', 7240, 'def _upsert_source('),
    # ⚠️ Widened from the bare `` `:6681` `` this pass first wrote (review cycle 6, F5): a bare `:NNNN`
    # is exactly the shape the comment above says is excluded — it is satisfied by any sibling spelling
    # of the same digits — and this row was the one of the four that carried it.
    (_AUDIT, '`expand_ops` preview at `:7190`', _SE, 7190, 'if expand_ops:'),
    (_AUDIT, '`:3062` derives the label', _SE, 3062, 'label = "Source" if entity_type =='),
    # --- three re-derived addresses that had NO pin at all, so a `+1` mutation left 441 tests green
    # (review cycle 6, P2-2). `grep -rn '28008\|28372' tests/ tools/` returned nothing before these rows:
    # the guard set is per-token, and an unlisted token is coverage the file only APPEARED to have.
    (_INDEX, '`sdk.py:28581` at this head', _SDK, 28581, 'WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL'),
    # ⛔ #14087 review round 7 (P3): the SAME `00_index.md:118` line writes a SECOND present-tense
    # this-head address — `the bullet is entity_verdict.py:43` — and no arm read it. Its sibling
    # (`sdk.py:28581`, the row above) was pinned, which is exactly how an unpinned neighbour hides:
    # `grep -c 'entity_verdict\.py:43' tests/test_12184_shifted_cites.py` -> 0 before this row, so a
    # re-point or a rotation of `entity_verdict.py` under it would have moved the address with every
    # arm green. One ROW per SITE, never one row per STRING.
    (_INDEX, '`entity_verdict.py:43`', _EV, 43, "writing `dedup_reviewed='link'` is worse than inert"),
    (_EVIDENCE, '(`sdk.py:28581`), so any non-NULL', _SDK, 28581, 'WHERE n.dedup_candidate = true AND n.dedup_reviewed IS NULL'),
    (_EVIDENCE, 'called at `sdk.py:28945`', _SDK, 28945, 'result = apply_entity_merge('),
    # --- row 2d's `:Meta` pair was the last `+21`-class cite in this document, and NOTHING pinned it.
    # The `#14038` re-cut wrote `sdk.py:62020`/`:62040` at `c5ca268aa96`, where `:62020` WAS the
    # `MATCH (m:Meta {key:$key}) RETURN m.recordedAt` statement and `:62040` WAS
    # `def _config_reset_state(`. A later fold took 21 lines out of `sdk.py` above both, so at this
    # head `:62020` is a DOCSTRING line and `:62040` is the `# #4282 Phase 2` banner — two cites that
    # read as re-derived and open on prose. Measured, not inferred:
    # `sed -n '61999p;62019p' tortoise/sdk.py` prints the read and the `def`. Both rows pin the CONTENT
    # the row's prose claims, because the population arms (`_PIN_EOF_PAIRS`, the census triple) are
    # INVARIANT under a re-point — they hold every number this pair can be moved to — and the drift
    # numerator cannot see them either (both sit past the pin's `sdk.py`, so they are non-comparable).
    # That is the whole uncovered-claim class: only opening the line catches it, so the line is pinned.
    (_AUDIT, '`sdk.py:61999` (reads)', _SDK, 61999, '"MATCH (m:Meta {key:$key}) RETURN m.recordedAt"'),
    (_AUDIT, '`sdk.py:62019` (`_config_reset_state`)', _SDK, 62019, 'def _config_reset_state(self) -> dict | None:'),
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
    (_AUDIT, "sdk.py:15873", _SDK, 15873, '"MATCH (m:GraphEventMeta) RETURN m.first_seq"'),
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
    (_AUDIT, '`:1292-1301`', _SSM, 1292, '_CONTROL_LEXICON_ROWS = ('),
    # --- #14043 review (finding 1, this cycle): §6.3's own two addresses were in neither list and
    # unpinned, and the header's default resolves them to the PIN — where `sdk.py:16077` is unrelated
    # prose and `sdk.py:3801` holds `sdk._graph_write_with_retry(` (the EMPTY line is the OLD `:3777`).
    # Both are one-token spellings already, so no figure
    # moves (345 as-written stays 345); NON-VACUOUS: `+1` reds the content arm at both.
    # ⛔ #14087 review: the span's END was one short — `:16081` is the paragraph's PENULTIMATE line and
    # `:16082` (the line reading "turns list is that same requirement for the record-held lane.") is its
    # last, with `:16083` blank. A span token pins only its START, so the short end was invisible to `_PINS`;
    # `test_the_jsonl_contract_span_ends_on_its_paragraphs_last_line` derives the end from the source
    # and grades it.
    (_AUDIT, 'sdk.py:16077-16082', _SDK, 16077, '**JSONL event log (#548):** written when *point* is provided'),
    (_AUDIT, 'sdk.py:3801', _SDK, 3801, 'sdk._emit_event("PointRetracted", _deleted_point_payload(tid))'),
    # --- #14038 review cycle 4 (P1): the header's "nine MORE addresses" enumeration names these three
    # SAME statements as BARE siblings ("`sdk.py`: `:15873`", "§6.3's `sdk.py`: `:16077-16082`",
    # "and `:3801`"). A bare token is not the string `sdk.py:15873`, so the long-form rows directly
    # above did NOT grade these three sites: the sentence promised "All nine are pinned by content"
    # while three of the nine had no row that could redden. The addresses were ALSO stale after the
    # conflict resolution `4ae073f96e5` — it took `main`'s rows and the branch's paragraph, so the
    # enumeration kept `:15842`/`:16046-16050`/`:3777` while the rows moved to the spellings that
    # carry those statements at this head. One ROW per SITE, as this file requires.
    # NON-VACUOUS, MEASURED both ways: re-pointing the DOC (`:15873`→`:15874`) reds the content arm AND
    # `test_no_pinned_token_can_be_satisfied_by_a_sibling_occurrence` (the token string is gone); moving
    # the ROW's line (`15873`→`15874`) reds the content arm and
    # `test_every_pinned_row_grades_a_line_inside_the_address_its_token_names` — **2 failed, 28 passed**
    # each way. An earlier form of this comment claimed the token arm "stays green"; it does not.
    (_AUDIT, '`:15873`', _SDK, 15873, '"MATCH (m:GraphEventMeta) RETURN m.first_seq"'),
    (_AUDIT, '`:16077-16082`', _SDK, 16077, '**JSONL event log (#548):** written when *point* is provided'),
    (_AUDIT, '`:3801`', _SDK, 3801, 'sdk._emit_event("PointRetracted", _deleted_point_payload(tid))'),
    # ⛔ THREE OF THE SIX BARE SITES IN THAT SAME SENTENCE WERE LEFT UNGRADED, which is the
    # sentence's own failure one level up: it says "All nine are pinned by content", and `:667`,
    # `:844` and `:916` had NO row — neither `_SPELLING` (`path.py:NNNN`) nor `_TWO_SPAN`
    # (`` `path` `` `:NNNN` ``) matches the bare `` `:NNNN` `` form, so nothing else could grade
    # them either. MEASURED: re-pointing each by `+1` left the whole file green (29 passed) while
    # the three siblings that DID get rows red immediately — a claim of coverage three sites
    # short. One ROW per SITE, and the token appears TWICE in the document for each.
    (_AUDIT, '`:667`', _LIVE, 667, 'EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")'),
    (_AUDIT, '`:844`', _HB, 844, '"MATCH (m:GraphEventMeta) RETURN max(m.last_seq), max(m.first_seq)"'),
    (_AUDIT, '`:916`', _HB, 916, '"MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = $first"'),
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
    ("sdk.py:24626", _SDK, 24626, "(n:Point {id:$id})-[:TAGGED]->()"),
    ("search_engine.py:7226", _SE, 7226, "RETURN op.id, type(r2) AS et, count(other)"),
    ("sdk.py:22267", _SDK, 22267, "s.summary=CASE WHEN $sum"),
    (":22390", _SDK, 22390, "s.summary, s.topics, s."),
    # ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): `hosted_api.py` IS touched by this
    # PR (+149/−35), so `main`'s pre-branch addresses are the ones that have moved. Measured on the
    # resolved tree: `sed -n '23615p' tortoise/hosted_api.py` -> `"MERGE (s:Source {url:$did}) "` and
    # `:23427` -> `def _execute_commit_writes(...)`, while `main`'s `23501`/`23313` read a prose comment
    # and a docstring line. This is the same measurement the earlier fold (`73cf057865`) took.
    ("hosted_api.py:23615", "tortoise/hosted_api.py", 23615, "MERGE (s:Source {url:$did})"),
    (":23427", "tortoise/hosted_api.py", 23427, "def _execute_commit_writes"),
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
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:21451-21453'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:21458'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:23062'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21433'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:21393'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:21321`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:32759'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:32776`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:43860`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:43797`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:32908-32909`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:32947`'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '(opening `:37527`'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '`:37546` is the `:Source` line'): 1,
    ('docs/architecture/SUBSTRATE-REBUILD-MAP.md', '(opening `:37567`)**)'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'cache_projection.py:3841'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:667`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:844`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:916`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:4950-4954'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7193'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7600'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7602'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7599-7602`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7605-7644`'): 1,
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
    # 4: the header's BARE spelling of the same address, the non-comparable enumeration in the drift
    # header (added by #13998 — `5bb3b32518` re-pointed this address past the pin's `mcp_server.py`,
    # which is what makes it a member of that set), plus the two BARE sites the #13991 branch itself
    # adds (§2.5's five-address enumeration and its `:11280` -> `:11590` provenance sentence) — see the
    # row's own comment for the four line numbers. Measured on the merged document:
    # `grep -o '`:11590`' docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | wc -l` -> 4.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:11590`'): 4,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5720`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:8515`'): 2,
    # The four bare `sdk.py` `:Tag` siblings the header paragraph enumerates, one occurrence each —
    # the §2.5 spellings are the one-token keys `sdk.py:31420` etc. and are a different key.
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
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'search_engine.py:7226'): 2,
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
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:23615'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:23427`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:34390`'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:22969-22970'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:25169-25170`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:25071-25075'): 3,
    # one ROW per SITE, never one row per STRING: the label-scan row writes this address BARE
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:25071-25075`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:22840-22841'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:23549'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:23549`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:23551-23553'): 2,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:23551-23553`'): 1,
    # the composed `turn_max_index` site (long-form ×3 + row 7's own wider range) and the LIST
    # endpoint's `actor_user_id` condition (long-form ×1, bare ×2 in the header clause)
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:45713-45725'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:45713-45730'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'hosted_api.py:51620'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:51620`'): 3,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:5434` at this head'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:7240` at this head'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`expand_ops` preview at `:7190`'): 1,
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
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:1292-1301`'): 2,
    # #14043 review (finding 1): one site each in §6.3. Measured on this document:
    # `grep -o -F 'sdk.py:16077-16082' <doc> | wc -l` -> 1, `... 'sdk.py:3801' ...` -> 1.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:16077-16082'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', 'sdk.py:3801'): 1,
    # #14038 review cycle 4 (P1): the header's "nine MORE addresses" enumeration names these three
    # as BARE siblings (one path, then `:NNNN`), which the long-form rows above do NOT cover — a bare
    # token is not the string `sdk.py:NNNN`, so the three sites could be re-pointed with every
    # long-form pin green. Measured on this document: `grep -o -F '`:15873`' <doc> | wc -l` -> 1, and
    # the same for '`:16077-16082`' -> 1 and '`:3801`' -> 1.
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:15873`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:16077-16082`'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`:3801`'): 1,
    # row 2d's `:Meta` pair — one SITE each, written once as a one-token spelling (the two-span forms
    # at `:135`/`:149` are different spellings and cannot satisfy these counts).
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:61999` (reads)'): 1,
    ('docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md', '`sdk.py:62019` (`_config_reset_state`)'): 1,
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
    which CAN be checked without history. The NUMERATOR (the drifter count) compares two historical
    blobs — `git show <head>:<path> | sed -n '<N>p'` at each of them, text for text — and this file NOW
    re-derives it too, in `test_the_drift_numerator_is_re_derived_from_the_pin_blob_not_carried` (and,
    in a shallow checkout, through that arm's history-free `comparable + incomparable == pairs`
    identity). So these literals are a change-detector for the RENDERING of the figures, not the only
    measurement of them.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    for claimed in (
        "167 of this document's 227",
        "dedupes the **345** as-written spellings to 227 — **118**",
        "345 written spellings → 227 pairs → 118 collisions → 167 drifters of the 227",
        # ⛔ The ARROW chain and the PROSE form are one claim: the cycle-3 re-cut
        # (`01a3c7072a`) restored the arrow rendering after the population
        # de-duplicated back to 227 pairs, so BOTH are asserted — a doc that
        # publishes one and not the other has lost a site, not just a phrase.
        # ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): these are the BRANCH edition's
        # figures, and they are re-MEASURED true of the resolved document (`_re_derived_drift` at this
        # tree: `(227, 215, 155, 167, 229, 217, 157, 169)`), which is why `main`'s `234`/`136` pair — the
        # figures its OWN edition of the header measured — is not carried here. A figure from another
        # edition of the document is a carried number, which is the failure this arm exists to catch.
        "**The drift is 167 of those 227.**",
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


#: The revision the header's census compares THIS head against — the pin its drift figure is measured to.
_DRIFT_PIN = "ccb49741b2ec"


def _git_resolves(spec: str) -> bool:
    """Does *spec* resolve in this checkout's object DB?"""
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", spec],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    return out.returncode == 0


def _path_in_tree(rev: str, path: str) -> bool | None:
    """Is *path* an entry of *rev*'s tree? `None` when that tree cannot be read.

    ⛔ ASKED OF `ls-tree`, NOT OF `<rev>:<path>`. `git rev-parse --verify -q '<rev>:<path>'` exits 1
    BOTH for a genuine absence AND for a missing TREE object, so a treeless partial clone
    (`filter=tree:0`) whose lazy fetch fails would be counted "absent at the pin" — inflating the
    drifter figure and inviting a false correction of a published number (#14038 review, P3).
    `ls-tree` separates them on the EXIT CODE: anything non-zero (`rc != 0` — measured 1 for a missing
    SUBTREE and 128 for a missing ROOT tree, `fatal: not a tree object`) means the tree could not be
    read and is the cannot-run class; rc 0 with EMPTY output is genuine absence; rc 0 with an entry is
    present. ⛔ Test `rc != 0`, never `rc == 1`: naming one code sends the commonest treeless shape —
    the root tree absent — down the absence path and recreates the false count this exists to stop.
    """
    out = subprocess.run(
        ["git", "ls-tree", rev, "--", path],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip() != ""


def _rev_is_readable(rev: str = _DRIFT_PIN) -> bool:
    """Can this checkout resolve the COMMIT *rev*? — the first of the three "cannot run" conditions.

    ⛔ SEPARATED FROM `_pin_blob_lines` ON PURPOSE. `git show <rev>:<path>` exits 128 for TWO
    different facts: this checkout cannot produce the object (no history in a `fetch-depth: 1`
    clone; a failed lazy fetch in a partial clone; a corrupt object), and the PATH does not exist
    at *rev* at all. The first is about the CHECKOUT and must NOT be counted: it is the cannot-run
    class, and what the arm does instead is grade the history-free identity (nothing skips — there is
    no `pytest.skip` in this file, see the arm's own note). The SECOND is about the
    ARTIFACT and must be graded — a module created after the pin is exactly the case a file:line
    census meets first (this PR adds `tortoise/capacity_notice.py`), and folding it into the skip
    would stop grading the numerator for the whole document while the message blamed CI history
    (#14038 review, P2). The header already counts that class as "not comparable", which is where
    such a path now lands.

    ⛔ HOW EACH CAUSE IS DECIDED, because a commit probe cannot answer a blob question: the commit
    probe below only establishes that `<rev>` is a commit this checkout holds. The PATH question is
    asked separately, by `_path_in_tree` — `git ls-tree`, never `<rev>:<path>`, which cannot tell an
    absent path from a missing tree. A path that IS in the tree but whose blob still fails to render
    is neither cause, so it stays in the CANNOT-RUN class (returns `None`, and the caller grades the
    history-free identity — no skip) — a BLOBLESS partial
    clone's failed lazy fetch would otherwise be counted as "absent at the pin", inflating the
    drifter figure and inviting a false correction of a published number, which is this file's own
    worst case (#14038 review, P3). See `_path_in_tree` for how the tree itself is asked, which is
    what keeps a TREELESS (`filter=tree:0`) clone out of that bucket too.
    """
    return _git_resolves(f"{rev}^{{commit}}")


@functools.cache
def _pin_blob_lines(path: str, rev: str = _DRIFT_PIN) -> list[str] | None:
    """*path*'s lines at *rev*; `None` when this checkout cannot produce them.

    THREE outcomes, and the difference between the last two is the whole point:
      * the lines — *path* is in *rev*'s tree and renders;
      * `[]` — *path* is genuinely ABSENT from that tree (an artifact fact, graded by the caller as
        not-comparable, never skipped);
      * `None` — the CANNOT-RUN class, on ANY of THREE causes: *rev* itself is unresolvable; *rev*'s
        TREE cannot be read (`_path_in_tree` returns `None` — a treeless `filter=tree:0` clone whose
        lazy fetch failed, a missing tree object, an unreadable rev); or the path IS in the tree but
        its blob will not render (a blobless clone's failed lazy fetch, a corrupt object). All three
        are facts about this CHECKOUT, so the arm does not count a pair it could not read: it returns
        `None` and the CALLER grades the history-free identity instead (no skip — #14038 cycle-6). ⛔ THREE, not two: the tree arm is the one the tri-state exists for, and dropping it
        leaves its `None` no slot — so the fall-through is "absent", which is the #14038 bug again.

    ⛔ MEMOISED per `(path, rev)` — the caller walks one entry per cited LINE, so without this the
    same module is re-rendered once per cite (measured at 227 `git show` calls over 53 distinct
    paths, the slowest arm in this file at 12.56 s; 53 calls after). ⚠️ That is a PERFORMANCE note,
    not a graded behaviour: the two call sites inside `_re_derived_drift` never vary `rev`, and the only
    callers that pass one are the tree-probe test's two graded asserts (`== []`, `is None`) — nothing
    asserts the memo itself, so NO mutation of this decorator can change a graded result. Deleting it
    only makes the suite ~7 s slower.
    Stated so the next reader does not read the memo as covered (#14038 review, rule 3).
    """
    if not _rev_is_readable(rev):
        return None
    in_tree = _path_in_tree(rev, path)
    if in_tree is None:
        # the tree object itself could not be read — cannot-run, never "absent".
        return None
    if not in_tree:
        # The TREE says the path is not in that revision — an artifact fact, graded not skipped.
        return []
    out = subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=REPO, capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        # The path IS in the tree but the blob would not render: an object this checkout cannot
        # produce — the cannot-run class, so this arm refuses the count rather than miscounting: it
        # returns None and the caller grades the history-free identity (no skip — see the arm's note).
        return None
    return out.stdout.splitlines()


def _re_derived_drift(text: str, repo: Path = REPO) -> tuple[int, ...] | None:
    """(pairs, comparable, drifted, drifters, alt_pairs, alt_comparable, alt_drifted, alt_drifters).

    The header's own rule, as code: a pair is COMPARABLE when its written line exists at the pin, and
    DRIFTED when that line's text differs at this head; the drifters are the drifted comparable pairs
    PLUS the ones that cannot be compared at all — which the header counts rather than hides.
    """
    _spellings, pairs = _resolved_pairs(text, repo)
    comparable = drifted = 0
    for path, line in pairs:
        pin = _pin_blob_lines(path)
        if pin is None:
            # cannot produce the pin's copy, on any of three causes: the rev is unresolvable; the
            # rev's TREE cannot be read (treeless clone, missing tree object); or the path IS in the
            # tree but its blob will not render. All are facts about THIS CHECKOUT, so this returns
            # `None` rather than COUNT a pair that was never read — and the caller then grades the
            # history-free identity instead of skipping (there is no `pytest.skip(` in this file: it
            # would assert NOTHING in the only place the rail reads it, `#13802`).
            return None
        head_file = repo / path
        if not head_file.is_file():
            # the head does not carry the module the document cites: an artifact fact, graded
            # below as "not comparable" rather than skipped.
            continue
        head = head_file.read_text(encoding="utf-8").splitlines()
        if not pin or line > len(pin):
            # absent at the pin (or past its end) — NOT comparable; the header counts these.
            continue
        comparable += 1
        if line > len(head) or pin[line - 1] != head[line - 1]:
            drifted += 1
    # ⛔ The ALTERNATIVE basis is RE-DERIVED from the union set, never `drifters + extra_pairs`: an
    # added pair can be UNDRIFTED, so that addition publishes a figure the tree never measured. Before
    # this, the only guard on it was a BAND around the addition (`#13599`), which accepts any numerator
    # the band still contains.
    union = pairs | _two_span_pairs(text, repo)
    alt_comparable = alt_drifted = 0
    for path, line in sorted(union):
        pin = _pin_blob_lines(path)
        if pin is None:
            return None
        head_path = repo / path
        if not head_path.is_file():
            continue
        head = head_path.read_text(encoding="utf-8").splitlines()
        if not pin or line > len(pin):
            continue
        alt_comparable += 1
        if line > len(head) or pin[line - 1] != head[line - 1]:
            alt_drifted += 1
    return (
        len(pairs),
        comparable,
        drifted,
        drifted + (len(pairs) - comparable),
        len(union),
        alt_comparable,
        alt_drifted,
        alt_drifted + (len(union) - alt_comparable),
    )


def test_the_drift_numerator_is_re_derived_from_the_pin_blob_not_carried() -> None:
    """The NUMERATOR is compared against a RECOUNT, not against itself — the `#14038` cycle-4 gap.

    `_figure_problems` re-derives only the DENOMINATOR, and says so in its own docstring. So when a
    later commit moved the population (231 pairs → 229) and updated the denominator while leaving the
    numerator alone, every arm stayed GREEN: the header read "159 of this document's 229" and
    "150 of the 222 comparable" while `222 + 9 = 231` contradicted the `229` beside it — and the stale
    numerator agreed with the stale comparable count, which is why internal agreement could not see it.

    This arm recomputes all four figures from `git show <pin>:<path> | sed -n '<N>p'` — the rule the
    header states — so a denominator-only edit reds HERE instead of shipping. That is the same class as
    the `#13599` finding (a figure nothing re-derived) one level up.

    ⛔ NOT A SILENT SKIP (#13802's ruling, `tests/test_docs_citation_refs.py:1219` — the INERT-ENFORCER
    shape). Every job that runs a suite checks out at `actions/checkout@v4`'s DEFAULT `fetch-depth: 1`,
    so that checkout carries NO history at all and the pin's object is simply absent there — which is
    why `pytest.skip` here would assert NOTHING in the only place the rail reads this file. When the pin is unreadable the arm therefore grades the HISTORY-FREE identity the
    shipped defect violated — `comparable + incomparable == pairs`, and `drifted + incomparable ==
    drifters` — which needs no history and reds the `#14038` cycle-4 figures exactly
    (`222 + 9 = 231` beside the 229-pair denominator it was published over). ⛔ The denominator in
    that example is the CYCLE-4 one, not this head's: the figures have since moved to 227 pairs, and a
    reader who checks `222 + 9` against 227 is checking a number the defect was never about. With
    history available this arm re-derives both bases in
    full, including the ALTERNATIVE one from the union set rather than `drifters + extra_pairs`.

    NON-VACUOUS, measured at this head BOTH ways: (a) restoring the pre-fix `159`/`150`/`222` while
    keeping the corrected denominator reds THIS test; (b) mutating `all 218 comparable ones` to `222`
    reds the SELF-CONSISTENCY arm (the `split.group(2) == comparable_said.group(1)` assert) — while the
    mutation that reaches the history-free IDENTITY is the pair denominator
    (`227 pairs` → `231`), which fails it with "the halves do not add up to the whole"; and (c) in a
    real `--depth 1` clone of this head the arm RUNS (no skip — `-rs` reports none) and passes on that
    identity; (d) mutating the SPLIT's own denominator (`146 of the 218 comparable` → `146 of the 300
    comparable`) reds the self-consistency arm in that same history-free shape — measured GREEN before
    that arm existed, because the with-history comparison which also catches it cannot run in a
    `fetch-depth: 1` checkout. ⛔ (b) NAMED THE WRONG ARM until this was measured: the two denominators
    are asserted EQUAL first, so a one-spelling edit is caught there and never reaches the identity.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    body = _unbolded(text)
    quadruple = _QUADRUPLE.search(body)
    assert quadruple, (
        "the header no longer publishes `N written spellings → P pairs → C collisions → D drifters of "
        "the M`, so this arm has no ratio to grade. Restore the sentence rather than deleting the arm."
    )
    _spellings, pairs, _collisions, drifters, _of = map(int, quadruple.groups())
    split = re.search(r"(?i)the drift is (\d+) of the (\d+) comparable, plus all (\w+) of the", body)
    comparable_said = re.search(r"all (\d+) comparable ones resolve at BOTH heads", body)
    drifted_said = re.search(r"(\d+) of the (\d+) comparable", body)
    assert split and comparable_said and drifted_said, (
        "the header no longer publishes the comparable SPLIT (`N of the M comparable, plus all W of the "
        "… not comparable` / `all M comparable ones`), which is the history-free half of this arm — the "
        "half a `fetch-depth: 1` checkout can actually grade."
    )
    incomparable_word = split.group(3).lower()
    assert incomparable_word in _NUMERALS, (
        f"the header counts the non-comparable addresses as {incomparable_word!r}, which is not in "
        "`_NUMERALS` — a count this arm cannot read is a count nothing grades."
    )
    # ⛔ `_as_int`, never `_SPELLED_NUMERALS.index`: that tuple carries `zero` AND `none` first, so an
    # index is off by one from `one` onward (`nine` → 10). Measured: the first cut used `.index()` and
    # red on a CORRECT header.
    incomparable = _as_int(incomparable_word)
    # (0) SELF-CONSISTENCY, and this is the arm the CI shape needs most: the comparable DENOMINATOR is
    # published in more than one sentence — the split's `N of the M comparable` and the `all M
    # comparable ones` that feeds the identity below — and ONE figure spelled twice is exactly the
    # shape the shipped defect took (`222 + 9 = 231` beside the 229-pair population it shipped over —
    # 227 is THIS head's pair count, and the two must not be quoted as one). ⛔ MEASURED, on a
    # shallow checkout (`_DRIFT_PIN` unresolvable, i.e. exactly what `actions/checkout`'s default
    # `fetch-depth: 1` produces): with the split's denominator mutated `218` → `300` this test stayed
    # GREEN — the only arm that compared the spellings is the WITH-HISTORY one below, which cannot run
    # there. So the half that runs in CI asserted a numerator agreement over a denominator it never read.
    # ⛔ TWO spellings, not three (#14038 review, P3, rule 3). `drifted_said`'s span is NESTED INSIDE
    # `split`'s on this document — it matches the very same sentence — so chaining its group (2) here
    # would be a leg no edit of this text can turn red. "A comment explaining why it cannot fail is NOT
    # a fix" is the rule, so the leg is DELETED rather than disclosed. `drifted_said` is still
    # load-bearing: its group (1) feeds the drifters identity below, so nothing stops being graded.
    assert int(split.group(2)) == int(comparable_said.group(1)), (
        "the header publishes its comparable denominator in more than one sentence — "
        f"{split.group(2)} in the split sentence and {comparable_said.group(1)} in `all … comparable "
        "ones` — and they are ONE figure. Re-measure it by content and update EVERY spelling; if these "
        "two name different sentences, re-point the patterns above rather than widening this arm."
    )
    # (1) HISTORY-FREE — the arm the CI shape gets. The halves must add up to the whole.
    assert int(comparable_said.group(1)) + incomparable == pairs, (
        f"the header publishes {comparable_said.group(1)} comparable + {incomparable} non-comparable "
        f"against a {pairs}-pair denominator — the halves do not add up to the whole. That is the "
        "`#14038` cycle-4 defect exactly (`222 + 9 = 231` beside a 229-pair population): a "
        "denominator-only edit leaves both halves stale and every arm green."
    )
    assert int(drifted_said.group(1)) + incomparable == drifters, (
        f"the header publishes {drifted_said.group(1)} drifted comparable + {incomparable} "
        f"non-comparable but a {drifters}-drifter numerator — the numerator and its own split disagree."
    )
    # (2) WITH HISTORY — re-derive both bases from the pin's blobs.
    derived = _re_derived_drift(text)
    if derived is None:
        return  # the identity above is the graded arm in a shallow checkout (#13802 forbids a skip)
    (
        pairs_d,
        comparable_d,
        drifted_d,
        drifters_d,
        alt_pairs_d,
        alt_comparable_d,
        alt_drifted_d,
        alt_drifters_d,
    ) = derived
    for claim in (
        f"{drifters_d} of this document's {pairs_d}",
        f"The drift is {drifters_d} of those {pairs_d}",
        f"all {comparable_d} comparable ones resolve at BOTH heads",
        f"{drifted_d} of the {comparable_d} comparable",
        f"{alt_pairs_d} pairs and {alt_drifters_d} drifters",
        f"{drifters_d} and {alt_drifters_d} are one measurement",
    ):
        assert claim in body, (
            f"the header does not carry {claim!r}, which is what THIS head measures against the pin "
            f"`{_DRIFT_PIN}` ({pairs_d} pairs, {comparable_d} comparable, {drifted_d} drifted, "
            f"{drifters_d} drifters; alternative basis {alt_pairs_d}/{alt_comparable_d}/"
            f"{alt_drifted_d}/{alt_drifters_d}). Re-measure the figure by the header's own rule and "
            "update BOTH the numerator and its cross-references — never adjust an older number by an offset."
        )


def test_the_tree_probe_grades_absence_and_an_unreadable_tree_DIFFERENTLY(tmp_path, monkeypatch):
    """The tri-state is the point of `_path_in_tree`, so it is graded, not merely described.

    ⛔ WHY THIS TEST EXISTS (#14038 review, rule 3). The docstring above says the whole reason
    `_path_in_tree` asks `git ls-tree` rather than `git rev-parse '<rev>:<path>'` is that the latter
    answers the SAME (False) for a genuine absence and for a tree object this checkout cannot read —
    and a review measured that reverting to it left this whole file GREEN, because every path the
    census walks is either present or genuinely absent in a COMPLETE checkout. A distinction no
    assertion can redden is a claim, not a guard; this constructs the one shape that separates them.

    The fixture is a commit whose root tree carries a `sub/` entry pointing at a subtree blob that
    does not exist (`git mktree --missing`), which is the treeless/`filter=tree:0` shape in
    miniature. Measured, in that fixture: `ls-tree <c> -- sub/x.py` exits **1** (an object it could
    not read) and `rev-parse --verify -q '<c>:sub/x.py'` ALSO exits **1** — indistinguishable from
    absence, which is exactly the bug. So:
      * absent from a HEALTHY tree -> `False` -> `[]`, graded not-comparable;
      * present in a tree whose subtree cannot be read -> `None` -> the cannot-run class, so the
        census does not COUNT a pair it could not read (the caller grades the history-free identity
        instead — nothing skips).
    """
    repo = tmp_path / "tiny"
    repo.mkdir()

    def git(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, input=stdin, check=False
        )

    assert git("init", "-q").returncode == 0
    assert git("config", "user.email", "census@example.invalid").returncode == 0
    assert git("config", "user.name", "census fixture").returncode == 0
    (repo / "present.py").write_text("x = 1\n", encoding="utf-8")
    assert git("add", "present.py").returncode == 0
    assert git("commit", "-q", "-m", "base").returncode == 0
    head = git("rev-parse", "HEAD").stdout.strip()
    blob = git("hash-object", "-w", "present.py").stdout.strip()

    # A root tree that REFERENCES a subtree object this repository does not hold.
    listed = git(
        "mktree",
        "--missing",
        stdin=f"040000 tree {'1' * 40}\tsub\n100644 blob {blob}\tlone.py\n",
    )
    assert listed.returncode == 0, listed.stderr
    made = git("commit-tree", listed.stdout.strip(), "-m", "root tree with a missing subtree")
    assert made.returncode == 0, made.stderr
    broken = made.stdout.strip()

    monkeypatch.setattr(sys.modules[__name__], "REPO", repo)
    _pin_blob_lines.cache_clear()
    try:
        # the healthy tree: present vs genuinely absent
        assert _path_in_tree(head, "present.py") is True
        assert _path_in_tree(head, "nope.py") is False
        assert _pin_blob_lines("nope.py", rev=head) == []  # graded, never skipped

        # the unreadable subtree: the arm the `rev-parse` probe cannot see
        assert _path_in_tree(broken, "sub/x.py") is None, (
            "a path whose TREE object cannot be read must be the cannot-run class, not 'absent'"
        )
        assert _pin_blob_lines("sub/x.py", rev=broken) is None

        # ⛔ THE GIT PREMISE THIS ROW RESTS ON — not a fixture guard. The graded claim is the `is None`
        # above; this states WHY only `ls-tree` can grade it. `rev-parse --verify '<rev>:<path>'`
        # answers False here — the same answer it gives for a genuine absence — so a re-implementation
        # using it returns `[]` and counts an unreadable pair as not-comparable, inflating the drifter
        # figure. ⚠️ No single edit of THIS FILE can turn this line red: the `is None` row above fires
        # first on any fixture change. It reds only if git itself changes how `<rev>:<path>` resolves an
        # unreadable subtree, which is a premise worth failing on loudly (#14038 review, P3).
        assert _git_resolves(f"{broken}:sub/x.py") is False, (
            "GIT PREMISE BROKEN: `<rev>:<path>` now distinguishes an unreadable subtree from an absent "
            "path, so the reason `_path_in_tree` asks `ls-tree` no longer holds and this row should be "
            "re-derived rather than kept"
        )
    finally:
        _pin_blob_lines.cache_clear()


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
#: The SECOND publication of the alternative numerator: "155 and 158 are one measurement under two counting
#: decisions". Graded against `_ALT_BASIS` in the history-free arm, because the two are ONE figure published
#: twice and a document contradicting itself about it is precisely `#14038`. It needs no pin and no history:
#: both mentions are IN the document, so the agreement holds in the shape CI actually runs.
_ONE_MEASUREMENT = re.compile(r"(\d+) and (\d+) are one measurement")
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

    Only the DENOMINATOR half is recomputed HERE (spellings, pairs, collisions, and the "N of those M"
    cross-references to them). This function grades the drifter numerator only for INTERNAL agreement —
    with the denominator, and across its four published mentions. ⛔ Its own re-derivation is NOT this
    function's job and does not "need history": `test_the_drift_numerator_is_re_derived_from_the_pin_blob_not_carried`
    re-derives it from the pin's blobs where the pin is readable, and grades the history-free
    `comparable + incomparable == pairs` identity where it is not — so this file has a numerator
    measurement in the `actions/checkout` default `fetch-depth: 1` shape, and an earlier form of this
    docstring claiming the numerator "needs history" was refuted by that arm (`#14038` fix round).
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
    # ⛔ FOUR mentions, not three (#14038 review, P2). The split sentence restates the ratio as
    # `**155 of the 227**` at its own tail, and NONE of the three patterns above reads it: measured,
    # mutating that tail `155` -> `157` left this whole file GREEN with `_figure_problems == []`. A
    # published number nothing re-derives is the exact defect class this file exists to catch, so the
    # fourth spelling is graded here rather than left to prose.
    for pattern, what in (
        (re.compile(r"(\d+) of this document's (\d+)"), "the `N of this document's M` header line"),
        (re.compile(r"The drift is (\d+) of those (\d+)\."), "the `The drift is N of those M` restatement"),
        (
            re.compile(r"(\d+) of the (\d+), and the ratio below"),
            "the split sentence's own `N of the M` restatement",
        ),
    ):
        found = pattern.search(body)
        if not found:
            problems.append(f"{what} is gone — the ratio is published in four places on purpose")
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
        # ⛔ HISTORY-FREE, and it closes the hole the comment below admitted: the alternative numerator is
        # published TWICE, and the two mentions must agree. Graded HERE, not in the pin arm, so it holds in a
        # `fetch-depth: 1` checkout — where the pin is unreadable and the only other grader of that sentence
        # cannot run. MEASURED before this leg existed, in a tree with no `.git`: `155 and 158` → `155 and
        # 156` — a document contradicting itself about a figure it publishes twice — passed every arm there
        # and red only where history was readable.
        one = _ONE_MEASUREMENT.search(body)
        if not one:
            problems.append(
                "the `N and M are one measurement` sentence is gone — it is the second publication of the "
                "alternative numerator, and the only history-free AGREEMENT between its two publications "
                "(the band and the two-code-span arm beside it grade the figure, not what the document says "
                "about it twice)"
            )
        else:
            one_drifters, one_alt_drifters = map(int, one.groups())
            if (one_drifters, one_alt_drifters) != (drifters, alt_drifters):
                problems.append(
                    f"the `{one_drifters} and {one_alt_drifters} are one measurement` sentence disagrees with "
                    f"the figures it claims to restate: the one-token basis reads {drifters} drifters and the "
                    f"alternative basis reads {alt_drifters} (`counting that form gives {alt_pairs} pairs and "
                    f"{alt_drifters} drifters`). One figure, two mentions, and they must be the same figure"
                )
        # ⛔ A BAND, not a lower bound. The two bases are one measurement under two counting decisions
        # (`155 and 158 are one measurement under two counting decisions, not two findings`), and each added
        # pair adds AT MOST one drifter — so the only legal numerators are `drifters` .. `drifters +
        # extra_pairs`. `alt_drifters < drifters` saw the downward violation and was blind to every
        # overstatement: a numerator inflated by +8 or +67 over the primary's read `problems == []`, which
        # is the exact direction a stale figure takes when a denominator moves under it (`#13599` review,
        # finding 1 — measured on this head before the fix). ⛔ The `It is the ONLY guard on this figure:`
        # sentence is DELETED rather than softened: the paragraph claimed exclusivity while the leg above it
        # grades the same figure against its second publication, so the block asserted two incompatible
        # things in the SAME comment block — six lines apart, measured, not "two" as this note first said
        # (#14038 review, P3: an unmeasured quantity stated as fact, in newly added text) — the class this
        # file exists to catch, reintroduced by the commit that
        # fixed the previous instance. It is also not the only guard on the figure:
        # `test_the_drift_numerator_is_re_derived_from_the_pin_blob_not_carried` grades the alternative
        # basis from the pin's union set, so an alt numerator this BAND still contains is caught there —
        # MEASURED on this head: `230 pairs and 158 drifters` → `… and 156 drifters` sits inside
        # `155..158`, passes this band, and reds that arm — and, since the mention leg landed above, ALSO
        # reds the `N and M are one measurement` leg (the document then states `158` and `156` for one
        # figure). "Nothing else moves" was true before that leg existed and is false now; the failure
        # therefore names the figure it is about rather than an unrelated row. ⛔ No pass TOTAL is
        # published here: an earlier draft of this
        # very sentence said "1 failed, 29 passed", and a peer's commit on the same branch added a test
        # one minute later, so the total was stale before it was pushed (`#14038`, the same rot as the
        # retired "0 of 112 rows"). The ARM is the claim; the total is a moving picture of the file.
        # The band bounds the figure; the re-derivation MEASURES it.
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
    345 → 350 — and so 118 → 123 collisions … while adding **no pair at all**: 227 either way, because
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
    # ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): this pair is the DOCUMENT's own
    # written address (`hosted_api.py`'s `:51620`, the `s.actor_user_id = $uid` condition, which this
    # branch moves `+114` past `main`'s 51,521-line copy). `main`'s `50935` belonged to its own edition of
    # the header, which the fold did not take; the derived set below is the grader either way.
    ("tortoise/hosted_api.py", 51620): 49851,
    ("tortoise/mcp_server.py", 11590): 11466,
    ("tortoise/sdk.py", 58099): 56415,
    ("tortoise/sdk.py", 58315): 56415,
    ("tortoise/sdk.py", 58317): 56415,
    ("tortoise/sdk.py", 58966): 56415,
    ("tortoise/sdk.py", 61999): 56415,
    ("tortoise/sdk.py", 62019): 56415,
    # ⛔ `#14278`'s re-point of §2.1's two `search_engine.py` adjacency rows DID add a pin-side fact: `:7193`
    # and `:7226` are past this pin's 7,071-line copy (their predecessors `:6837`/`:6891` were not), so the
    # split's denominators moved with them. Missing those two entries is how the table stayed
    # self-consistent while the split published `223 / eleven / 130 / eight` about a document that measured
    # `221 / thirteen / 128 / eight`: `expected_comparable` is derived FROM this table, so an
    # under-enumerated table can never disagree with itself. `test_the_non_comparable_set_is_derived_from_the_pins_file_lengths`
    # closes that by deriving membership from `_PIN_FILE_LENGTHS` instead.
    # ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): main's four `search_engine.py` rows
    # are the ones the resolved DOCUMENT writes (its body carries `:7193`/`:7226`/`:7600`/`:7602`), so the
    # branch's four pre-merge addresses are dropped rather than kept beside them. The set is then GRADED by
    # `test_the_non_comparable_set_is_derived_from_the_pins_file_lengths`, not trusted.
    ("tortoise/search_engine.py", 7193): 7071,
    ("tortoise/search_engine.py", 7226): 7071,
    ("tortoise/search_engine.py", 7600): 7071,
    ("tortoise/search_engine.py", 7602): 7071,
}
#: The pin's own file lengths — the MEMBERSHIP RULE behind the split, kept per FILE rather than transcribed
#: per pair (`git show ccb49741b2ec:<path> | wc -l`). A pair is non-comparable exactly when its line is past
#: its own file's length at the pin, which is a fact about the census and the pin and nothing else, so it can
#: be DERIVED on every run instead of being copied into `_PIN_EOF_PAIRS` by whoever re-points a cite. That
#: transcription is what shipped the split wrong twice (`#14246` review finding 1, then `#14278`'s own `+23`
#: re-point): both editions were internally consistent, and the second one was consistent with a table that
#: had stopped describing the document.
_PIN_FILE_LENGTHS: dict[str, int] = {
    "tortoise/sdk.py": 56415,
    "tortoise/search_engine.py": 7071,
    "tortoise/mcp_server.py": 11466,
    "tortoise/hosted_api.py": 49851,
    # Declared because the document cites it at `:22118`, i.e. above the ceiling below — it holds NO
    # non-comparable pair (its pin copy is 22,857 lines), and that is the point: the ceiling arm asks for a
    # declared length from every file cited high enough to hide an EOF crossing, not from every file.
    "tortoise/projection/__init__.py": 22857,
}
#: The split's three published sentences. Each is matched on the header's emphasis-stripped body, like every
#: other figure pattern in this file, and each group is graded as a NUMBER (`_as_int`), so an edition may
#: state a count in digits or in words without the comparator reporting a false absence.
_SPLIT_COMPARABLE = re.compile(r"all (\d+) comparable ones resolve at BOTH heads")
_SPLIT_NONCOMPARABLE = re.compile(r"the (\w+) that are NOT comparable")
_SPLIT_DRIFT = re.compile(r"the drift is (\d+) of the (\d+) comparable, plus (\w+) of those (\w+) pairs")
#: ⛔ `(?:do|does)` — the sentence is graded at the count it STATES, so a correct correction from a
#: plural to a singular count must not read as "the sentence vanished". The arm's own message says to
#: re-point this pattern rather than delete the arm, and the singular is the shape the truth takes
#: here (`#14038` review cycle 13: exactly one of the twelve is past `main`'s copy of its file).
_SPLIT_ABSENT_AT_MAIN = re.compile(r"the\s+(\w+) that (?:do|does) not exist at `main` either")


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
    (2) The header NAMES each of them at the address it writes, somewhere inside the graded region —
    the two anchors are quoted here EXACTLY as the code finds them: `that are NOT comparable` to
    `past the pin's copy of its file`.
    ⛔ WHICH ARM CATCHES WHAT — by MECHANISM, not by paraphrasing which sentence those anchors land in.
    An earlier cut of this paragraph paraphrased that three times and was wrong each time, because where
    the two strings LAND in the prose is not what a reader assumes. This paragraph now asserts nothing
    about it: it quotes the strings, and the arm's own `find` calls are the reference.
    · arm 1 reds on a cite RE-POINTED elsewhere in the document — the pair leaves the census population
      and the static table becomes an orphan. Arm 2 cannot see that, because the region text is
      untouched. Ten of the twelve pairs are that case; the two that also carry a one-token spelling
      inside the region red both arms.
    · arm 2 reds when the REGION carries none of the three accepted forms for a pair. A pair may
      therefore be missing from the enumeration sentence and still satisfy this arm, provided the region
      writes it somewhere else — which is why this arm must never be described as grading "the
      enumeration".
    ⛔ And it is NOT "the only place the twelve are written": `11590` alone also appears at five sites
    outside the region, so this arm grades a REGION and never claims uniqueness across the prose.
    (3) The published
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
    # sibling behind an adjacent path (`search_engine.py`'s `:7600`).
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
    # must add up to the population `_PIN_EOF_PAIRS` measures.
    drifters_incomparable = _as_int(drift.group(3))
    absent_at_main = _as_int(absent.group(1))
    # ⛔ THAT IDENTITY ALONE IS NOT ENOUGH, and it is the whole finding (#14038 review cycle 13, P2): it
    # relates two PUBLISHED numbers to each other, so it agreed with itself on the word `zero` for as long
    # as the sentence said zero — while `origin/main`'s own file sizes refute zero (`hosted_api.py` is
    # 51,521 lines there against the twelve's `:51620`). A relation between two claims cannot catch a wrong
    # claim. So `absent_at_main` is compared against `main`'s BLOBS below, and the decomposition against
    # the header's TOTAL.
    assert drifters_incomparable + absent_at_main == len(_PIN_EOF_PAIRS), (
        f"the split says {drifters_incomparable} of its non-comparable pairs drift and {absent_at_main} "
        f"exist at neither head — {drifters_incomparable + absent_at_main} in all, but the pin-side facts "
        f"measure {len(_PIN_EOF_PAIRS)} non-comparable pairs. Every one of them is in exactly one half."
    )
    # …and the decomposition has to ADD UP to the numerator the header publishes, which is the third number
    # the two-term identity above cannot see: `155 of the 215 comparable` + the twelve = 167.
    _total_m = re.search(r"The drift is (\d+) of those (\d+)", body)
    assert _total_m is not None, (
        "the header no longer publishes the drift as `The drift is <N> of those <M>` — this arm reads the "
        "TOTAL from it to hold the decomposition to it, so re-point the pattern rather than dropping the arm")
    _drift_total = int(_total_m.group(1))
    _comparable_drift = int(drift.group(1))
    assert _comparable_drift + drifters_incomparable + absent_at_main == _drift_total, (
        f"the header's decomposition does not add up to its own total: {_comparable_drift} comparable "
        f"drifters + {drifters_incomparable} non-comparable + {absent_at_main} existing at neither head = "
        f"{_comparable_drift + drifters_incomparable + absent_at_main}, but it publishes `The drift is "
        f"{_drift_total}`. A decomposition that omits the pair it just disclosed is how `eleven of those "
        "twelve` shipped beside a 167 that only twelve can reach."
    )
    # The disclosure is graded against `main`'s BLOBS where this checkout can read them; where it cannot
    # (a `fetch-depth: 1` clone), the arms above still grade the sentence and nothing skips.
    _main_sizes: dict[str, int] = {}
    _main_readable = True
    for _path, _line in _PIN_EOF_PAIRS:
        _blob = _pin_blob_lines(_path, "origin/main")
        if _blob is None:
            _main_readable = False
            break
        _main_sizes[_path] = len(_blob)
    # ⛔ THE SKIP IS NAMED, never silent (#14038 review cycle 7, P2 — pre-existing, and the same class
    # as the failed-diff arm below). Every arm in this function that needs `origin/main`'s BLOBS is
    # gated on `_main_readable`, which goes False when any of them would not render — and a gate with
    # no excuse reports green without measuring. The COMMON legitimate cause is an ABSENT `origin/main`:
    # the `test` job checks out at the default `fetch-depth: 1`, so the ref does not exist there and
    # these arms cannot run. That state skips.
    # ⛔ If the ref RESOLVES and a blob still would not read, the arm must not report green — the four
    # main-side claims would be green WITHOUT MEASURING. It must not claim ONE cause either: this state
    # has TWO and the arm cannot tell them apart. (a) `_pin_blob_lines`'s own tri-state classifies it a
    # CANNOT-RUN fact about this checkout — a blobless/partial clone whose lazy fetch failed, or a corrupt
    # object — which no change to the diff can fix. (b) A readable tree failing to render a path `ls-tree`
    # lists IS a defect in this arm. Excusing (a) would also excuse (b), i.e. the false PASS this guard
    # exists to prevent, so the state REDS — and the message names BOTH causes and how to tell them apart
    # rather than asserting the arm is broken.
    if not _main_readable:
        _main_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "origin/main"],
            cwd=REPO, capture_output=True, text=True, check=False)
        assert _main_ref.returncode != 0, (
            "`origin/main` RESOLVES ("
            f"{_main_ref.stdout.strip()[:12]}) but one of its blobs would not render, so every "
            "main-side arm here — the file lengths, the absent-at-main count, the provenance pair "
            "and the write measurement — reported green WITHOUT MEASURING. TWO causes are possible "
            "and this arm cannot distinguish them: (a) THIS CHECKOUT cannot render blobs — a "
            "blobless/partial clone whose lazy fetch failed, or a corrupt object (`_pin_blob_lines` "
            "classifies that as a cannot-run fact about the checkout, not a verdict on the PR): fetch "
            "the blobs and re-run; or (b) the tree is readable and a path `ls-tree` lists will not "
            "render — a defect in this arm. The only excused state remains an ABSENT `origin/main` "
            "(a `fetch-depth: 1` checkout), never green over an unmeasured claim."
        )
        # ⛔ THE EXCUSED RUN SAYS SO, and it says so in a channel pytest does not swallow (#14038
        # review cycle 9, P1). A green run printed the same thing whether eight arms measured or four
        # were skipped — so a reader of a depth-1 log could not tell "the main-side claims hold" from
        # "the main-side claims were never read". ⛔ `print` does NOT fix that here: capture attaches a
        # passing test's stdout to the call report only, and the `python-ci` test legs run `-r fEs` with
        # default capture and no `-s` (not EVERY leg in the repo passes `-r fEs` — the e2e jobs pass
        # `-rs` — so the claim is scoped to the legs this file runs in), so the line never reaches
        # `$PYTEST_LOG` — the trap this repo has already
        # measured twice (`tests/conftest.py` #1103 and #7735, and `tests/_tmpdir_hygiene.py`'s answer:
        # a warning IS visible under those flags). So: `warnings.warn`, no assertion change.
        warnings.warn(
            "[14038] `origin/main` is ABSENT in this checkout (a `fetch-depth: 1` clone), so the "
            "four main-side arms in this function were SKIPPED rather than satisfied: the "
            "`origin/main` file lengths, the absent-at-main count, the provenance pair and the "
            "write measurement. The doc-TEXT arms (the ownership clause, the drift decomposition "
            "and the twelve-pair enumeration) still graded the sentence.",
            # ⛔ NO `stacklevel=2` (#14038 review cycle 10, P3; corrected in cycle 13):
            # from inside a test it attributes the warning to pytest's own frame
            # (`_pytest/python.py:167`) — NOT the file:line this line exists to
            # surface. Default `stacklevel=1` names THIS file, which is the
            # attribution an operator wants. (The commit that added `=2` meant
            # VISIBILITY, not attribution — it is orthogonal to what it chose.)
            # It is spelled out because `ruff`'s B028 requires an EXPLICIT
            # stacklevel — the value is the default, so nothing changes but the
            # lint red (`ruff check .` is a required job and a new violation in
            # a new file reds it).
            stacklevel=1,
        )
    if _main_readable:
        _measured_absent = sum(1 for _path, _line in _PIN_EOF_PAIRS if _line > _main_sizes[_path])
        assert absent_at_main == _measured_absent, (
            f"the split says {absent_at_main} of the non-comparable pairs do not exist at `main` either, "
            f"but `git show origin/main:<path> | wc -l` measures {_measured_absent} of the "
            f"{len(_PIN_EOF_PAIRS)} whose written line is past `main`'s copy of its file "
            f"({', '.join(f'{p.rsplit(chr(47), 1)[-1]}:{line}>{_main_sizes[p]}' for p, line in _PIN_EOF_PAIRS if line > _main_sizes[p]) or 'none'}). "
            "A pair whose address `main` does not carry is not a pair `main` drifts — say the measured "
            "count, and do not restore the partition this arm used to assert."
        )

    # ⛔ THE PROVENANCE OF THE ONE PAIR THAT IS NOT `main`'s, GRADED TOO (#14038 review cycle 13, P2).
    # The header used to call it "this branch's own insertion (`+108` above the commit door)"; measured,
    # the branch inserts NO such line and the shift is 114. Those are four separate claims — the delta,
    # the two absolute addresses, and the file growth — and the sentence that states them must be the
    # measurement of this head, not a number carried from the cut before it.
    _prov = re.search(
        r"this branch's own \(`\+(\d+)` above the commit door\) MOVE of it — its statement sits at "
        r"`main`'s (?:line )?([\d,]+) and at this head's (?:line )?([\d,]+), matching "
        r"`hosted_api\.py`'s ([\d,]+) -> ([\d,]+) lines",
        body,
    )
    assert _prov is not None, (
        "the header no longer states, in the graded form, that the one non-comparable `main`-absent pair "
        "is MOVED by this branch rather than inserted. That sentence carries the provenance of the one "
        "pair a reader cannot check from the pin — keep it, and keep its five figures, or re-word this "
        "arm against the wording you use."
    )
    _prov_delta = int(_prov.group(1))
    _prov_main_line, _prov_head_line = (int(g.replace(",", "")) for g in _prov.group(2, 3))
    _prov_main_len, _prov_head_len = (int(g.replace(",", "")) for g in _prov.group(4, 5))
    _head_hosted = (REPO / "tortoise/hosted_api.py").read_text(encoding="utf-8").splitlines()
    assert _prov_head_len == len(_head_hosted), (
        f"the header says `hosted_api.py` is {_prov_head_len} lines here; the file is "
        f"{len(_head_hosted)}. A file-length figure is the whole basis of this split — re-derive it.")
    assert _prov_main_line + _prov_delta == _prov_head_line, (
        f"the header says the statement moved from `main`'s {_prov_main_line} to {_prov_head_line} "
        f"here — a `+{_prov_delta}` — but the two addresses differ by {_prov_head_line - _prov_main_line}. "
        "Either the delta or an address is stale, and the delta is what names the branch's own lines.")
    # ⛔ Keyed by FULL path (`tortoise/hosted_api.py`), so a bare-name membership test is always
    # False and this whole main-side half is DEAD CODE that reports green (#14038 cycle 14, P1:
    # `"hosted_api" in _main_sizes` never held, and a `51,507 -> 51,622` header plus a
    # `main` address on `params["uid"] = actor_filter` both stayed green). Resolve the key, or
    # this arm is the inert enforcer it exists to catch.
    _main_hosted_key = next((_k for _k in _main_sizes if _k.endswith("hosted_api.py")), None)
    assert _main_readable is False or _main_hosted_key is not None, (
        "`origin/main` was readable and its `hosted_api.py` measured, but no key in "
        f"`_main_sizes` ends with `hosted_api.py` (keys: {sorted(_main_sizes)}) — the "
        "main-side provenance arms below would silently not run."
    )
    if _main_readable and _main_hosted_key is not None:
        _main_hosted = _pin_blob_lines("tortoise/hosted_api.py", "origin/main") or []
        assert _prov_main_len == len(_main_hosted), (
            f"the header says `main`'s `hosted_api.py` is {_prov_main_len} lines; it measures "
            f"{len(_main_hosted)}. The delta above is `main`-relative, so both ends of it must be measured.")
        assert len(_head_hosted) - len(_main_hosted) == _prov_delta, (
            f"the header calls the move `+{_prov_delta}` this branch's own lines, but the branch's "
            f"`hosted_api.py` is {len(_head_hosted)} lines against `main`'s {len(_main_hosted)} — "
            f"{len(_head_hosted) - len(_main_hosted)}. Re-derive the delta rather than carrying it.")
        assert _prov_main_line <= len(_main_hosted) and _prov_head_line <= len(_head_hosted), (
            f"the provenance sentence names an address past the end of the file it attributes it to "
            f"(`main` {_prov_main_line} of {len(_main_hosted)}, head {_prov_head_line} of "
            f"{len(_head_hosted)}) — that is the exists-at-neither-head error one level down.")
        assert (_head_hosted[_prov_head_line - 1].strip()
                == _main_hosted[_prov_main_line - 1].strip() != ""), (
            f"the header says the statement at `main`'s line {_prov_main_line} is the SAME statement as "
            f"the one at this head's {_prov_head_line}, which is what makes it a move and not an "
            f"insertion. Re-derive both addresses.")
    # ⛔ The head ADDRESS is pinned, not merely same-statement: this branch's 114 inserted lines sit
    # ABOVE `main`'s 51,506, so every pair `(n, n+114)` inside that block is a same-statement pair with
    # the right arithmetic — at that head a header could state `main`'s 51,494 (`params["uid"] = actor_filter`) and
    # this head's 51,608 and stay GREEN (#14038 cycle 15, P2 — MEASURED: that doc passed this file while
    # the same-statement and arithmetic arms both held). The split counts ONE pair for this file, so the
    # provenance sentence must name THAT one.
    _pin_hosted_lines = [_ln for (_p, _ln) in _PIN_EOF_PAIRS if _p.endswith("hosted_api.py")]
    assert _prov_head_line in _pin_hosted_lines, (
        f"the provenance sentence names this head's line {_prov_head_line}, but the split counts "
        f"hosted_api.py's non-comparable pair at {_pin_hosted_lines} — the sentence must move THAT "
        "statement. An unpinned address is the one thing the same-statement and arithmetic arms above "
        "cannot catch, because a whole block of them moves together.")
    # ⛔ WHO OWNS THE TWELVE, GRADED (#14038 review cycle 15, P2). The header said "the twelve that are
    # NOT comparable are this branch's own" — FALSE for eleven of them: this branch writes into
    # `tortoise/hosted_api.py` and nowhere else among the four files, so it cannot own addresses in
    # `sdk.py`, `mcp_server.py` or `search_engine.py`. `main`'s copy of the sentence DID qualify the
    # attribution ("ten of them this branch's own, the eleventh moved past the pin by `main`'s #14169"),
    # and the rewrite dropped the qualification — the paragraph that exists to repair carried figures
    # carried an unqualified claim instead. No arm read this clause, which is why it stayed green.
    _owning = re.search(
        r"the (\w+) that are NOT comparable are addresses past the pin's copy of their own file, of "
        r"which exactly (\w+) is this branch's own write", body)
    assert _owning is not None, (
        "the split no longer states, in the graded form, that the non-comparable addresses are past the "
        "pin's copy of their own file and that exactly ONE of them is this branch's own write. The "
        "unqualified form ('the twelve ... are this branch's own') is false: this branch's diff touches "
        "`tortoise/hosted_api.py` only. Keep the qualification, or re-word this arm."
    )
    assert _as_int(_owning.group(1)) == len(_PIN_EOF_PAIRS), (
        f"the ownership clause names {_owning.group(1)} non-comparable pairs while the split counts "
        f"{len(_PIN_EOF_PAIRS)}"
    )
    assert _as_int(_owning.group(2)) == 1, (
        f"the ownership clause says {_owning.group(2)} of the {len(_PIN_EOF_PAIRS)} is this branch's own "
        "write; exactly one of them can be, because the branch's diff is `tortoise/hosted_api.py` only."
    )
    if _main_readable:
        # MEASURED, not asserted from the sentence: the branch writes into none of the other three files.
        _numstat = subprocess.run(
            ["git", "diff", "--numstat", "origin/main...HEAD", "--", "tortoise/sdk.py",
             "tortoise/mcp_server.py", "tortoise/search_engine.py"],
            cwd=REPO, capture_output=True, text=True, check=False)
        if _numstat.returncode != 0:
            # ⛔ A NAMED CANNOT-RUN, never a silent drop (#14038 review cycle 6, P2). `_main_readable`
            # above proves `git show origin/main:<path>`, which does NOT prove a MERGE BASE: the
            # three-dot form needs one, and the `test` job checks out at the default `fetch-depth: 1`.
            # The old `if _numstat.returncode == 0:` had no else, so this arm — the one that MEASURES
            # whether the branch writes into the three files it calls `main`'s — reported green
            # without running. A failed diff is now excused only when `git merge-base` cannot find the
            # base either; with a base in hand, a failed diff is a defect in this arm, not an
            # environment fact, and it reds.
            _merge_base = subprocess.run(
                ["git", "merge-base", "origin/main", "HEAD"],
                cwd=REPO, capture_output=True, text=True, check=False)
            assert _merge_base.returncode != 0, (
                f"`git diff --numstat origin/main...HEAD` exited {_numstat.returncode} although "
                f"`git merge-base origin/main HEAD` resolved the base "
                f"({_merge_base.stdout.strip()[:12]}) — the arm measuring whether this branch writes "
                "into the three files it calls `main`'s would silently not run: "
                f"{(_numstat.stderr or '').strip()[:300]}"
            )
        else:
            assert _numstat.stdout.strip() == "", (
                "the split says the eleven non-comparable addresses outside `hosted_api.py` are `main`'s "
                f"own drift, but this branch's diff writes into them:\n{_numstat.stdout}"
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


# ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): BOTH sides of this hunk are different
# tests and both are kept. The branch's `test_the_epic_bundles_i7_prose_cites_the_range_its_row_carries`
# (#14038 cycle 13, P3) is not on `main`; `main`'s `test_the_non_comparable_set_is_derived_from_the_pins_
# file_lengths` (`#14278`) is not on the branch. Neither supersedes the other — dropping either is a lost
# guard, which is the defect class both exist for.
def test_the_epic_bundles_i7_prose_cites_the_range_its_row_carries() -> None:
    """Prose that quotes a document's line range must be the range the document carries at THIS head.

    ⛔ WHY THIS CROSS-FILE ARM EXISTS (#14038 review cycle 13, P3). `04-plan.md`'s I7 row is graded
    structurally in `tests/test_7949_capstone_measure.py`, which parses whatever range the row carries —
    so the COMMENT BLOCKS beside that guard, which quote the same range in prose, were graded by nothing.
    They read `:15557-15634` while the row had already been re-derived to `:15563-15640` by the commit
    that moved it, and the same blocks still attributed the move to "this branch's own 108 inserted
    lines" when `hosted_api.py` grows 114 against `main`. That is this file's own subject one level down:
    a figure re-measured in one place and CARRIED in another.

    ⛔ IT LIVES HERE, NOT THERE, on purpose: `test_7949_capstone_measure.py`'s `durations` row pins that
    file's own `^def test_` count in handwritten prose, so adding a case there would demand a re-write of
    that row's long enumeration for no benefit — and an arm placed INSIDE the existing bundle test would
    be INERT the moment that function reds — and a guard that is dead exactly when its sibling is
    failing is the shape this file exists to catch, whatever the cause of the sibling's red.
    (Measured at this head: that sibling is green, `1 passed, 443 deselected`; the placement is a
    structural argument, not a claim about its current colour. Cycle 14 corrected this line,
    which had asserted the red as a fact.) A separate case in this file always runs.

    Head-free on purpose: the rule is "the citation labelled `at this head` equals the row's", so it
    needs no history, no `main` and no pin — and it cannot pass by agreeing with itself, because the ROW
    is the authority and the COMMENT is the claim.
    """
    _plan = REPO / "docs/epics/2026-11-04-7949-extractor-v4-capstone/04-plan.md"
    _rows = [ln for ln in _plan.read_text(encoding="utf-8").splitlines() if "| **I7** |" in ln]
    assert len(_rows) == 1, (
        f"I7 must be exactly ONE line of `04-plan.md` to scope its range cite to, found {len(_rows)} — "
        "re-word this guard rather than letting it bind the easier of two")
    _cite = re.search(r"`hosted_api\.py:(\d+)-(\d+)`", _rows[0])
    assert _cite is not None, (
        "`04-plan.md` I7's range cite moved or was re-spelled — re-derive BOTH endpoints from "
        "`hosted_api.py` and keep the `:START-END` form, which is what this guard parses")
    _start, _end = int(_cite.group(1)), int(_cite.group(2))
    _src = (REPO / "tests/test_7949_capstone_measure.py").read_text(encoding="utf-8")
    _here = re.findall(r"`:(\d+)-(\d+)` at this head", _src)
    assert _here, (
        "no `:START-END` in the bundle test is labelled `at this head`, so this arm cannot run — keep the "
        "label on the citation that names the CURRENT head's range, do not delete the arm")
    for _start_s, _end_s in _here:
        assert (int(_start_s), int(_end_s)) == (_start, _end), (
            f"the bundle test calls `:{_start_s}-{_end_s}` the range 'at this head', but `04-plan.md`'s "
            f"I7 row carries `:{_start}-{_end}`. A citation labelled for this head that disagrees with the "
            "document is the re-derived-number-carried-instead-of-re-derived class: re-derive it from "
            "the row, or label it with the head it was actually taken at.")

def test_the_non_comparable_set_is_derived_from_the_pins_file_lengths() -> None:
    """MEMBERSHIP IS DERIVED, not transcribed — the arm the split was missing.

    WHY THIS EXISTS. `_PIN_EOF_PAIRS` was the split's only source of membership, and the arm above derives
    `expected_comparable = len(pairs) - len(_PIN_EOF_PAIRS)` FROM it — so an under-enumerated table agrees
    with itself perfectly. That is not hypothetical: `#14278` re-pointed §2.1's three `search_engine.py`
    adjacency rows and two of the new addresses (`:7193`, `:7226`) crossed this pin's 7,071-line copy,
    while two addresses it re-pointed the other way (`:7600`/`:7602`) crossed `main`'s 7,441-line copy, so
    the true split became 221 comparable / thirteen non-comparable / 128 drifters among the comparable /
    eight of the thirteen / five at neither head — and the header went on publishing `223`/`eleven`/`130`/
    `eight`/`three` over exactly that document, with every arm green (`138` too, which the same measurement
    moves to `136`). The four figures were again internally consistent and again wrong: `223 + 11 == 234`
    and `130 + 8 == 138` held, exactly as they did in the `#14246` edition this table was written to stop.

    WHAT IT GRADES. A pair is non-comparable iff its line is past ITS OWN file's length at the pin — a fact
    of the census and the pin, not of anyone's transcription. So the set is computed from `_PIN_FILE_LENGTHS`
    and the table built from it must be that set, in both directions (a pair left out of the table cannot
    hide, and a pin-side fact invented for a pair the document no longer writes cannot linger). The two
    structures are also held to agree on the length each file states, so they cannot drift apart.

    RESIDUAL, stated rather than claimed closed: a file NOT in `_PIN_FILE_LENGTHS` is checked by a CEILING —
    no undeclared file may be cited at a line above the shortest declared length — because this file cannot
    know its `wc -l` at the pin. Below that ceiling a short file's own EOF is invisible here; measured at
    this head only `tortoise/projection/__init__.py` was cited above the ceiling without a declaration, it
    was declared, and every census pair past its file's pin-EOF now lives in a declared file.

    NON-VACUOUS: on `058f5555b4`'s edition of the document the derived set is 11 pairs while this table now
    holds 13, so the first arm reds; and flipping one document number across an EOF (e.g. `:7193` ->
    `:7050`, which IS comparable at 7,071 lines) removes it from the derived set and reds the same arm.
    """
    text = (REPO / _AUDIT).read_text(encoding="utf-8")
    _spellings, pairs = _resolved_pairs(text)

    floor = min(_PIN_FILE_LENGTHS.values())
    undeclared = sorted({path for path, line in pairs
                         if line > floor and path not in _PIN_FILE_LENGTHS})
    assert not undeclared, (
        f"the document cites {undeclared} above the shortest declared pin length ({floor} lines), so a "
        "pair in one of them could be past that file's own EOF and this arm could not see it. Declare the "
        "file's `git show ccb49741b2ec:<path> | wc -l` in `_PIN_FILE_LENGTHS` — the ceiling is what makes "
        "the undeclared-file residual finite rather than silent."
    )

    for (path, _line), pin_lines in _PIN_EOF_PAIRS.items():
        assert _PIN_FILE_LENGTHS.get(path) == pin_lines, (
            f"`_PIN_EOF_PAIRS` says `{path}` is {pin_lines} lines at the pin while `_PIN_FILE_LENGTHS` says "
            f"{_PIN_FILE_LENGTHS.get(path)} — the two structures describe one pin, so they must agree; "
            "otherwise membership is derived from one fact and published from another."
        )

    derived = {(path, line) for path, line in pairs
               if path in _PIN_FILE_LENGTHS and line > _PIN_FILE_LENGTHS[path]}
    missing = sorted(derived - set(_PIN_EOF_PAIRS))
    stale = sorted(set(_PIN_EOF_PAIRS) - derived)
    assert not missing and not stale, (
        "the non-comparable split is not what the document's own cites measure at this pin — "
        f"past-EOF pairs the table omits: {missing}; table entries the document no longer writes past its "
        f"file's EOF: {stale}. `_PIN_EOF_PAIRS` is transcribed by hand while the census is derived, so a "
        "re-point that crosses an EOF (or one that moves back under it) changes the split with nothing "
        "red — which is how this header published `223 / eleven / 130 / eight / three` over a document "
        "measuring `221 / thirteen / 128 / eight / five`. Re-derive the table AND the four published "
        "figures from the measured split; do not adjust one to the other."
    )

    # ⛔ THE ONE NUMERAL THE ARMS ABOVE LEAVE UNGRADED: the split's comparable-drift numerator
    # ("128 of the 221 comparable"). `_SPLIT_DRIFT`'s first group was read by NOBODY — the comparability
    # arm grades its denominator, its non-comparable numerator and the absent-at-main count, and
    # `_QUADRUPLE` grades the headline — so `130` sat beside a `138` that no longer decomposed into it for a
    # whole edition (`130 + 8 == 138` was true of the corpus before this branch's re-point, and the pair of
    # figures it left behind kept the relation while describing a different document). The identity is the
    # grader: the two halves must add up to the headline, which is what makes the fourth figure derived
    # rather than quoted.
    body = _unbolded(text)
    drift = _SPLIT_DRIFT.search(body)
    quad = _QUADRUPLE.search(body)
    absent = _SPLIT_ABSENT_AT_MAIN.search(body)
    assert drift is not None and quad is not None and absent is not None, (
        "the split's numerator relation needs all three published sentences (the `the drift is N of the M "
        "comparable, plus S of those T pairs` restatement, its `the Z that does not exist at `main` either` "
        "sibling, and the `S spellings → P pairs → C collisions → D drifters` headline); one of them no "
        "longer parses, so re-point this arm at how it is written now"
    )
    comparable_drift = _as_int(drift.group(1))
    # ⛔ MERGE RESOLUTION (#14038 fold of `origin/main` `cc936d47db`): the non-comparable DRIFTERS are the
    # ones that drift at `main` PLUS the ones that exist at neither head — which is exactly the convention
    # `_re_derived_drift` implements (`drifters = drifted comparable + every non-comparable pair`). The
    # edition of this arm on `origin/main` summed only the drift sentence's third group, which was true of
    # `main`'s own header because that header's headline (`136 = 128 + 8`) likewise excluded its five
    # absent-at-neither pairs; this branch's header counts them (`155 + 11 + 1 = 167`), so the arm has to
    # read the third term it published, or it grades a definition the measurement below does not use.
    # `test_the_comparability_split_names_every_pair_past_the_pins_eof` asserts the same decomposition.
    incomparable_drift = _as_int(drift.group(3)) + _as_int(absent.group(1))
    headline = int(quad.group(4))
    assert comparable_drift + incomparable_drift == headline, (
        f"the split says {comparable_drift} of the comparable pairs drift and {incomparable_drift} of the "
        f"non-comparable ones do (drift-at-`main` plus the ones at neither head), which is "
        f"{comparable_drift + incomparable_drift} — but the headline says {headline} drifters of the "
        f"{quad.group(5)}. The two halves are the whole population, so a re-point that moves a pair across "
        "the pin's EOF must move all three figures together"
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

    What cannot rot is the RELATION each mutant breaks, and this list is now written in TABLE ORDER so the
    two can be audited against each other — it had one item the table does not contain and omitted two the
    table does (`#14038` review, P2). Measured: "an alternative basis LEVEL WITH THE PRIMARY" is a state the
    guard ACCEPTS — the band's lower edge is inclusive, so `alt_drifters == drifters` is legal by
    construction, and a document carrying it measures `problems == []` here and at the pre-leg head — so it
    was never a broken relation. The rows it omitted are the alternative basis one pair short of what the
    two-code-span form measures, and the alternative basis reading fewer drifters than the primary. Ten
    rows, ten items:

      1. a pair count and its collision arithmetic one edition behind (the `triple`);
      2. collisions that stop following the pair count they are arithmetic on;
      3. a ratio naming a denominator it does not carry;
      4. one of the ratio's published mentions disagreeing with the others (the loop grades three of the
         four; the fourth is the mention-agreement leg below);
      5. an alternative basis one pair short of what the two-code-span form measures, not level with it;
      6. an alternative numerator inflated past what its added pairs can contribute;
      7. an alternative basis reading FEWER drifters than the primary — adding pairs cannot remove one;
      8. the SECOND publication of that alternative numerator disagreeing with the first;
      9. a two-code-span pair count that DISAGREES with what the document measures (below it at this
         edition);
     10. one more of those pairs claimed uncounted than the union adds.

    Each is applied to the sentence AS SHIPPED, by capture-group position.

    ⛔ The alternative basis is mutated on BOTH capture groups (`:1` pairs, `:2` drifters) on purpose: the
    drifter half used to be read and thrown away (`_alt_drifters`), so the guard on it was never exercised by
    the control that exists to prove it works — which is how a one-sided bound survived a round that found
    two other reachability defects here. ⚠️ That sentence used to call it "the only figure in this sentence
    without a second witness"; the alternative numerator now HAS one — the `N and M are one measurement`
    sentence, graded by the history-free leg — so the item is past-tensed rather than left standing false.
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
    one_measurement = _ONE_MEASUREMENT.search(text)
    for found, what in ((triple, "triple"), (quadruple, "figures sentence"), (ratio, "`N of this document's M`"),
                        (alt, "alternative basis"), (one_measurement, "`N and M are one measurement`"),
                        (two_total, "two-code-span pair count"),
                        (two_new, "two-code-span uncounted count")):
        assert found is not None, (
            f"the shipped document no longer carries the {what}, so this control has nothing to mutate — "
            "re-point `_TRIPLE`/`_QUADRUPLE`/`_ALT_BASIS`/`_ONE_MEASUREMENT`/`_TWO_SPAN_TOTAL`/"
            "`_TWO_SPAN_NEW` at how it is published now"
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
            "one of the three GRADED mentions of the ratio disagrees with the others",
        ),
        (
            alt,
            {1: alt_pairs - 1},
            "the alternative basis is one pair short of what the two-code-span form measures, not level with it",
        ),
        # ⛔ WHY THIS MUTANT IS DERIVED FROM THE BAND'S EDGE, AND NOT A FIXED OFFSET — kept here because
        # the loop's assertion messages interpolate `why`, and a measurement table inside one of them
        # buries the mismatch a failing row is trying to report (`#14038` review). The row shipped a
        # literal `{2: alt_drifters + 7}` and the two distances it needs — the band's SLACK, and how far the
        # literal sits above it — are properties of the DOCUMENT.
        # ⛔ TWO different distances, named here so they are not swapped again: the band's SLACK below its
        # edge is `extra_pairs - (alt - primary)` — MEASURED 0 or 1 on the 35 editions that publish the
        # alternative basis (of the 44 reachable from this head; `extra_pairs` is 1, 3 or 4 there against
        # a gap of 1 or 3, and 0 on the nine that predate the two-code-span disclosure, where neither
        # distance exists) —
        # and the `+7` literal sits ABOVE the edge by `7 - slack`, i.e. 6 or 7, which is the figure this
        # rationale once published under the slack's name (0 is not 6). So the literal never went legal here.
        # ⛔ TWO counts must not be confused: 3 UNCOUNTED added pairs here (the `_TWO_SPAN_NEW` arm's
        # numeral, `**three**`), and 5 two-code-span pairs IN TOTAL — the document writes that total
        # UNBOLDED, `The document also writes five pairs as two code spans` (its only `**five**` is
        # unrelated, §2.5's `:Tag` count), so grep the sentence, not the marker — that `5` is
        # `3 uncounted + 2 already counted`, so it is neither the band's slack nor the literal's distance
        # above the edge, and must not be read as either. That misreading is how this rationale first
        # claimed the branch 'had already carried 5'; the uncounted count STATED anywhere in those 44
        # editions is 3 or 4 (never 1) — though only 18 of them state one at all, the rest predating that
        # sentence — while measured `extra_pairs` there is 0, 1, 3 or 4 — the 0s are the nine editions
        # above — and the only editions carrying 5 are on `feat/13971-point-strip`, which is not an
        # ancestor here. ⚠️ Those are ONE scope with ONE set of values: an earlier form of this block
        # printed `extra_pairs` `1..4` in one sentence and "never left 3..4" eight lines later for the same
        # quantity (`:1586` grades the two equal), so a reader got two ranges under one heading.
        # A fixed `+offset` therefore goes legal when `extra_pairs - (alt_drifters - drifters) >= offset`,
        # which is `extra_pairs >= 10` at this head, NOT `>= 7`: at extra_pairs=8 the band is `155..163`
        # and the `+7` value 165 is still outside it, and at 9 it is `155..164` against 165 — both
        # measured — and only at 10 does the band reach 165 and fall silent, leaving the band's RULE-2
        # assertion to red blaming a band that is present and right (#14038 review, P3).
        (
            alt,
            {2: _drifters + two_span_uncounted + 1},
            "the alternative basis's numerator is overstated past what its added pairs can contribute — the "
            "direction a stale figure takes when a denominator moves under it. ⛔ DERIVED from the band's "
            "own edge (`primary + extra_pairs`), not a fixed `+7`; `two_span_uncounted` is graded equal "
            "to `extra_pairs` by the `_TWO_SPAN_NEW` arm, so this anchor is always exactly one above the "
            "upper edge (the measurements behind it are in the comment above)",
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
            one_measurement,
            {2: alt_drifters - 2},
            "the SECOND publication of the alternative numerator disagrees with the first — and the mutant "
            "is anchored two INSIDE the band on purpose, so the disagreement is the ONLY thing wrong with "
            "the mutated document and its red cannot be explained away as an out-of-band figure",
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
        if match is one_measurement:
            # ⛔ RULE 2's PARSEABILITY half — and NOT the band's, which is what this block claimed until it
            # was measured. This mutant edits the MENTION sentence; the band grades the `counting that form
            # gives …` sentence, which the mutation never touches, so the band cannot fire here at ALL,
            # whatever the figures become. MEASURED on this head, and on a re-measured document carrying the
            # band's inclusive lower edge (`alt == primary`, `problems == []`): the shipped mutant reports the
            # mention-disagreement message and nothing else, and the lower-edge mutant (`155 and 153`) still
            # reports only that message. So there is no "passes on the BAND's red" state to guard against;
            # what CAN go wrong is the mutant itself — a value the pattern cannot read (a `-1` after a
            # re-measurement that puts the alternative numerator at one) reds through the ABSENT-SENTENCE
            # arm, and the row would pass without the agreement comparator ever being consulted. That is the
            # false PASS of the control the count rows' RULE 2 forbids, so it gets the same treatment: the
            # mutated sentence must still parse, its new figure must be the one the pattern grades, and the
            # red must NAME the leg.
            rematched = _ONE_MEASUREMENT.search(_unbolded(mutated))
            assert rematched is not None, (
                f"the control for {why!r} produced {stale!r}, which `_ONE_MEASUREMENT` cannot read, so the "
                "red would come from the absent-sentence arm — fix the mutant VALUE, do not relax this"
            )
            assert int(rematched.group(2)) == next(iter(overrides.values())), (
                f"the control for {why!r} rewrote a figure the pattern does not grade: "
                f"{rematched.group(2)!r} vs {next(iter(overrides.values()))!r}"
            )
            mention_problems = _figure_problems(mutated)
            assert any("sentence disagrees with the figures it claims to restate" in problem
                       for problem in mention_problems), (
                f"the control for {why!r} red for some other reason ({mention_problems}), so the "
                "mention-agreement leg was never consulted and the row proves nothing about it"
            )
            # ⛔ The claim above, PINNED rather than asserted in prose: the band reads the alternative-basis
            # sentence, so a mention-only mutation cannot reach it. If a later edit re-points the band at the
            # mention sentence (or this mutation starts hitting two sentences), the mechanism described above
            # is no longer the mechanism, and the comment must be re-measured instead of trusted.
            assert not any("can add at most" in problem for problem in mention_problems), (
                f"the band filed a problem for a MENTION-only mutation: {mention_problems} — the block above "
                "says the band grades the `counting that form gives …` sentence and cannot see this mutant, "
                "so re-measure that claim rather than deleting this assertion"
            )
        if match is alt and 2 in overrides:
            # ⛔ RULE 2 FOR THE BAND, and it was needed the moment the mention leg landed: the two rows
            # that mutate the alternative numerator (`+extra_pairs+1`, and `_drifters - 1`) red TWICE, and
            # the only observation the table makes is `!= []`. MEASURED at the PRE-FIX head
            # `9decb729185f` — before the assertion below existed — with the band arm deleted (its `if`
            # -> `if False:`) the control stayed GREEN, while the same edit at the pre-leg
            # `d37d356e2ffd` reds it: the mention leg had taken over the entire red those two rows
            # produced, so the band was proven by nothing, and reverting it to the one-sided lower bound
            # (`alt_drifters < drifters`, the `#13599` finding-1 defect) went unnoticed in this file. At
            # THIS head those edits red, via this assertion — it is the thing that closes them. The red
            # must therefore NAME the band, not merely exist.
            assert any("drifters against the primary basis's" in problem
                       for problem in _figure_problems(mutated)), (
                f"the control for {why!r} red for some other reason ({_figure_problems(mutated)}), so the "
                "BAND was never consulted — the mention-agreement leg supplies this row's red on its own, "
                "and deleting the band (or reverting it to a one-sided lower bound) would go unnoticed"
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
    `min..max` over every number turned such a token into a range, so the arm accepted the `58337`
    the token never mentions — the exact false PASS this arm exists to refuse, one indirection
    further out. Measured on the shipped tree: 0 of the rows this arm scans (`_PINS` ∪
    `_MAIN_SET_MISSED`) carries more than one address, so
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
    inside = (_AUDIT, "`:7599-7602`", _SE, 7602, "MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point)")
    assert not _rows_outside_their_token((inside,)), "a line inside the named range must pass"


def test_a_token_naming_two_addresses_does_not_vouch_for_the_hull_between_them() -> None:
    """Negative control — the fail-open `min..max` left in this arm, re-opened here.

    On the PRE-FIX code `_token_span` returned `(min(numbers), max(numbers))` over EVERY number a
    token carries, so a token naming two unrelated addresses became a range spanning the gap: the
    arm accepted the `58316` neither address names. That is the same false PASS this arm exists to
    refuse — a row asserting an address the document does not carry — so it is refused here rather
    than recorded as a residual. Measured on the shipped tree: 0 of the rows this arm scans (`_PINS` ∪
    `_MAIN_SET_MISSED`) carries more than one
    address, so no row's verdict moves.
    """
    between = (_AUDIT, "`:58315`/`:58317`", _SDK, 58337, "rows = proj.g.query(")
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
    never read. Measured on the shipped tree: 0 of the rows this arm scans has a token with no address.

    ⛔ NO ROW COUNT HERE ON PURPOSE: an earlier form said "0 of 112", then the peer lane added three
    `_PINS` rows and the sentence was silently two rows stale — the same unverified-literal class this
    file exists to catch, in prose (the `#14038` cycle-5 review's finding, applied to the count's own
    text rather than to a figure). The arm scans whatever `_PINS ∪ _MAIN_SET_MISSED` holds.
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
    # 62 -> 63 after #13879's merge: §2.5's `:Tag` row cites `mcp_server.py:11590`, re-derived from the
    # pin-head `10730`, and NOTHING read it — the same class as the `:6989` and `hosted_backup.py:2683`
    # rows above.
    # 63 -> 64 at the #13992 review (P2): the SAME address is written TWICE in the document, and the
    # row above pins only the §2.5 spelling (`mcp_server.py:11590`) — the header enumerates it BARE as
    # `:11590`, where no row and no count reached. Reverting just the header half left every arm green,
    # measured above. One ROW per SITE, never one row per STRING: `:8515`/`:5720` on that same header
    # line are the neighbours that already carry their own bare-token row.
    # 68 -> 72 at the #14043 review (finding 3): four addresses the revision writes at THIS head were
    # in neither the header's enumeration nor `_PINS`, so the header's reader rule sent a reader to the
    # pin, where none of them opens. Same shape as the `:6989` / `hosted_backup.py:2683` rows above:
    # one ROW per SITE, and an unlisted token is coverage this file only appeared to have.
    # 72 -> 77 at the SAME review's finding 2: the class was still unclosed for FIVE addresses this
    # revision writes — the new §2.2 `:Source` reader row's `session_source_prose_migration.py`
    # cites (`:218-223`, `:1263-1268`, `:274-281`) and row 13's inner pair (`:277-279`) plus its
    # `_CONTROL_LEXICON_ROWS` read (`:1292-1301`). ⛔ Those five are PIN-STABLE, not head-relative: one
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
    # 82 -> 91 at this lane's review (86, then five more for the sibling cites on the same rows) of the #14038 delta: three addresses on the `:CommitRecord`
    # row and in §7 were pinned NOWHERE, so they rotted green — `hosted_api.py:22436-22437` opened
    # on an unrelated comment (the statement is `:22544-22545`), `:24592-24593` on a
    # `store.discard_attempt` comment (`:25169-25170`), and the standalone `:Session`
    # `value_nodes_held` statement `:24493-24497` on a quota comment (`:25071-25075`, cited at FOUR
    # sites — three long-form, one bare). Same shape as the `:6989` / `hosted_backup.py:2666` rows:
    # one ROW per SITE, and an unlisted token is coverage this file only appeared to have.
    # 91 -> 95: the `8f7724b593c` review's two "reported, not touched" sites — the composed
    # `turn_max_index` range (§2.4 ×2, §6.1, row 7 — the last two write a WIDER range, so its own
    # row) and the LIST endpoint's `actor_user_id` condition (long-form in §2.4, bare twice in the
    # header's comparability clause). Both had rotted to `45028-45045` / `:50935`, both still
    # resolved at `ae26adf963`, and NEITHER had a row that could redden. Same shape again.
    # 102 at the #14087 review round 7 (P3), on the merged tree: `00_index.md`'s entity-verdict row
    # carries a SECOND present-tense this-head address on the line this PR edits
    # (`entity_verdict.py:43`), and NOTHING read it — its `sdk.py:28581` sibling was pinned and the
    # neighbour was not, so a rotation of `entity_verdict.py` under that cite would have moved it
    # silently. Same shape as the `:6989` / `sdk.py:28085` / `hosted_backup.py:2683` rows above.
    # 107 at the #14038 review (cycle 2, P2 ×2), RESTORED after the `69898b2866` re-cut silently
    # dropped these five rows while keeping their occurrence counts (measured: the merge's own conflict
    # resolution took the older side of this file): five addresses that NOTHING read — the audit's heal
    # scan + its stamp-back (`hosted_api.py:21451-21453` / `:21458`) and the SUBSTRATE sentence's three
    # OPENING values (`:37527`, `:37546`, `:37567`), which a forward merge had resurrected at +21 while
    # the two graded headlines stayed right.
    # 107 -> 109 at the `#14038` fix lane's row-2d pass: the `:Meta` pair (`sdk.py:62020`/`:62040`) was
    # the LAST `+21`-class cite in this document, and it was unobservable to EVERY population arm —
    # `_figure_problems` is invariant under a re-point (345/229/116/169 hold at any address this pair
    # takes), and the drift numerator cannot see it because both addresses sit past the pin's `sdk.py`
    # and are therefore non-comparable. Only opening the line catches this class, so the line is pinned.
    # 109 -> 113 at the follow-up lane: the two rows the previous fix lane NAMED but did not establish
    # (2d's `_find_point_by_content` callers, 14's payload-`mentions` read and its `pt` binding) are
    # re-pointed by content and pinned — four rows, each anchored on the line its row claims.
    # 113 -> 119 at row 2d's own pass: the row's five remaining stale addresses were re-pointed by
    # CONTENT and pinned, plus `sdk.py:32759` — which was already correct and which a hand-off table on
    # this PR told lanes to MOVE, i.e. the false-correction shape pinned rather than repeated. The
    # population arms are invariant under this edit (measured: 345/227/215/167 and 229/217/157/169
    # identical before and after), which is why only opening the lines can grade it.
    assert len(_PINS) == 119


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
    ("sdk.py:42486", "tortoise/sdk.py", 42486, "\"MATCH (s:Source)-[:references]->(o:Source) RETURN s.url, o.url\",", 1),
    ("sdk.py:58966", "tortoise/sdk.py", 58966, "\"MATCH (s:Source {url:$url})-[:references]->(n) RETURN count(n)\",", 2),
    ("sdk.py:20141-20144", "tortoise/sdk.py", 20144, "\"AND coalesce(n.is_episodic, false) <> true \")", 2),
    ("sdk.py:20149", "tortoise/sdk.py", 20149, "_stamp_capture_provenance(", 2),
    ("sdk.py:58315-58339", "tortoise/sdk.py", 58317, "\"s.turn_count, s.actor_user_id, s.harness, \"", 6),
    ("sdk.py:58317-58339", "tortoise/sdk.py", 58317, "\"s.turn_count, s.actor_user_id, s.harness, \"", 1),
    ("sdk.py:58317", "tortoise/sdk.py", 58317, "\"s.turn_count, s.actor_user_id, s.harness, \"", 3),
    ("sdk.py:4265-4268", "tortoise/sdk.py", 4265, "\"s.client_captured_at_source=CASE WHEN s.client_captured_at IS NULL \"", 3),
    ("sdk.py:4399-4406", "tortoise/sdk.py", 4404, "\"RETURN s.turn_max_index, s.turn_count, collect(t.id)\",", 2),
    ("sdk.py:2898", "tortoise/sdk.py", 2898, "\"SET s.capture_redactions=$redactions \"", 1),
    ("sdk.py:31533", "tortoise/sdk.py", 31533, "\"MATCH (t:Tag) \"", 1),
    ("sdk.py:31551", "tortoise/sdk.py", 31551, "\"MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) \"", 1),
    ("sdk.py:24513", "tortoise/sdk.py", 24513, "\"MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) \"", 1),
    ("sdk.py:24857", "tortoise/sdk.py", 24857, "\"MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t\"),", 1),
    ("sdk.py:27800", "tortoise/sdk.py", 27800, "\"MATCH (n:Point {id:$id}) RETURN properties(n)\",", 3),
    (":28699", "tortoise/sdk.py", 28699, "\"MATCH (n:Point {id:$id}) RETURN properties(n)\",", 3),
    (":30357", "tortoise/sdk.py", 30357, "f\"MATCH (n:Point) WHERE {where} RETURN properties(n)\",", 3),
    (":30453", "tortoise/sdk.py", 30453, "f\"MATCH (n:Point) WHERE {where} RETURN properties(n)\"", 3),
    (":30552", "tortoise/sdk.py", 30552, "\"MATCH (n:Point {id:$id}) RETURN properties(n)\",", 4),
    (":44181", "tortoise/sdk.py", 44181, "\"RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim\",", 3),
    ("entities.py:1292", "tortoise/projection/entities.py", 1292, "_POINT_JSON_RECORD_PROPS: frozenset = frozenset({\"mentions\"})", 2),
    ("entities.py:2068-2076", "tortoise/projection/entities.py", 2068, "# once. MEASURED at #10389's review head, before this entry: one SDK", 1),
    ("entities.py:2506", "tortoise/projection/entities.py", 2506, "\"operator\", \"provenance\", \"about_entities\", \"aboutEntities\",", 1),
    ("entities.py:2555", "tortoise/projection/entities.py", 2555, "_POINT_DENY: frozenset = frozenset({", 1),
    ("entities.py:2415-2416", "tortoise/projection/entities.py", 2416, "# out first, because the wipe destroys the only copy (\u00a717.2, `#11325`)", 1),
    ("entities.py:2419-2420", "tortoise/projection/entities.py", 2420, "# `update_point(<id>, story_arc=None)` \u2014 #11325, and only on the rows ", 1),
    ("entities.py:3177", "tortoise/projection/entities.py", 3177, "set_clauses.append(\"n.sourceVersionTransit=$sv\")", 1),
    ("entities.py:3982", "tortoise/projection/entities.py", 3982, "\"MERGE (s:Session {id:$sid}) SET s.is_episodic=true\",", 1),
    (":3295-3297", "tortoise/projection/entities.py", 3297, "self._POINT_HANDLED | self._POINT_DENY,", 2),
    (":3339", "tortoise/projection/entities.py", 3339, "for key in self._POINT_DENY - self._POINT_HANDLED:", 2),
    (":3389", "tortoise/projection/entities.py", 3389, "or key in self._POINT_DENY or key in self._META_KEYS:", 2),
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


#: The `g.query(` call that OPENS a statement, as `_read_captured_session` and its neighbours write it.
#: Used only to bound a span: a row cites ONE statement, so a second opener between the anchor and the
#: span's END means the END landed in the next statement.
_QUERY_OPENER = "g.query("


def test_the_13854_range_spans_end_inside_the_statement_their_anchor_opens() -> None:
    """A span must not run past the end of the statement its anchor sits in.

    ⛔ MEASURED DEFECT this arm exists for (#14038, after the `+47e28b549e` fold): the merge moved
    every `sdk.py` line at or below 5,736 by **−21**, and a re-derivation that shifted a range's
    START while leaving its END moved the cited closing line 21 lines DOWN — `sdk.py:58336-58360`
    became `58315-58360`, which opens on `rows = proj.g.query(` and now closes on
    `extracted_count = proj.g.query(`, a DIFFERENT statement (`:58345`). Every other arm in this
    file stayed green on it: the token arm only counts occurrences, the anchor arm only asks that
    the anchor lie inside the range, and the fragment arm reads one line. The correct end is
    `:58339` (`"s.turn_max_index",`, the last query-string line — the same line `:58360` read at the
    pre-merge head), derived by CONTENT, not by carrying the number.
    """
    for token, path, line, _fragment, _expected in _REDEFINED_13854:
        found = re.search(r":(\d+)-(\d+)$", token)
        if not found:
            continue
        end = int(found.group(2))
        target = (REPO / path).read_text(encoding="utf-8").splitlines()
        # Lines AFTER the anchor, through the span's closing line: a statement may open on the
        # anchor itself (a range that starts on its own `g.query(`), so the anchor is excluded.
        reopened = [
            number
            for number in range(line + 1, min(end, len(target)) + 1)
            if _QUERY_OPENER in target[number - 1]
        ]
        assert not reopened, (
            f"{token!r} closes on {path}:{end}, but a new statement opens at {reopened} inside the "
            f"span (anchor {path}:{line}) — the range's END was carried instead of re-derived. Move it "
            f"by the same MEASUREMENT that moved the start (grep the row's own closing fragment)."
        )
