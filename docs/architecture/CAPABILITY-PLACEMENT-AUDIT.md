---
title: "Capability-first placement — the census: every stored shape, its readers, and whether a graph operation needs it (#13083)"
type: architecture
domain: platform
doc_status: draft
created: 2026-11-17
aboutSubjects: Tortoise memory graph, epistemic-team
aboutObjects: "#13083, #13082, #10017, #10070, #10000, #10429, #13094, #11758, #9025, #12458, #13024, STORAGE-ARCHITECTURE.md §9.1"
ownedBy: epistemic-team
related:
  - issue: 13082
    note: "The roadmap this is slice P0-a of. Phase 1/2 (migrating the Cypher readers) is explicitly NOT this document."
  - issue: 13083
    note: "The issue this answers. It moves what nothing reads; it does not rewrite what something does."
  - doc: docs/architecture/STORAGE-ARCHITECTURE.md
    note: "§9.1 (D1) is the precedent AND the mechanism; §3's per-write test is this document's vocabulary."
  - doc: docs/evidence/13083-capability-census/README.md
    note: "The measurement this document's byte columns come from, with its instrument and its limits."
---

# The capability census

**The rule this document applies** (owner ruling 2026-11-16, `#13082`): *anything no graph query reads
is Postgres-only by default, and staying in the graph is something a shape must justify.*

**The question asked of every shape, and the only question:**

> Does a **graph-capability operation** read it — variable-length path traversal · pattern matching over
> unknown adjacency · the fulltext index · EP state (a belief walk) · the vector index?
>
> **Not** "does a Cypher statement happen to mention it". A point lookup, a filtered listing, an
> aggregate and an ordered range scan are **SQL-shaped**; they are executed by FalkorDB today and they
> would execute anywhere. A row kept in RAM for a SQL-shaped reader is paying **584× the price of disk**
> (`#13082`: $73/GB-month resident vs $0.125/GB) for a latency a Postgres index also buys.

**Why the audit is the lever, and not a preference.** The field sizes graph memory for the **working
set** — the data and indexes the production query mix actually touches — not for whole-graph residency,
and the acceptance criterion is a **sustained cache-hit ratio** (Neo4j publishes `>98%`,
`dbms.page_cache.hit_ratio`, with heap and OS headroom reserved). A shape no graph operation reads is
not a *slow* member of the working set: it is **not in it at all**, and its only effect is to dilute the
ratio the budget is sized against. See §8 for what was taken from outside and what was checked in-repo.

**Method — the two censuses that worked, repeated.** `#12458` classified every `:GraphEvent` reader at
`file:line`; `#13024` proved the allocator unreachable with a call-site census. This document does the
same for **stored shapes**, and it states its population and its commands so a later head can re-derive
every number rather than trust it.

**The census wrote every `file:line` below at ONE head: `ccb49741b2ec`** — the branch head this document was
written and reviewed at (its merge of `origin/main` `c4f6170cc302`); the census landed on `main` as the
squash commit `c06d6289e9`, and `ccb49741b2ec` is reachable as `refs/pull/13095/head` (`git fetch origin
'refs/pull/13095/head:ccb49741b2ec'`), so every cite the census wrote can be opened there.
⚠️ **That is no longer true of EVERY cite below, and the honest fix is to name both heads rather than to
pick one.** A later pass has re-derived a **named** set of addresses at `main`; they are enumerated in one
place in this header. **The `main` group is exactly two things: the sixteen below, and the `goal_judgement.py`:1128 entry (which its own sentence names at `main`, and whose line is identical at both heads)** — those open at
**`ae54df166d`**, the `main` this revision was integrated onto. `.py` lines DID move under it (`#13290`
re-derived the `search_engine.py` group), so the head is named here rather than inferred, and every entry of
that group was re-opened AT IT. **The rule a reader applies is mechanical, and it is exact for the addresses
IN the `main` group of that enumeration: an address in it opens at `main` `ae54df166d`.**
⚠️ **Every OTHER entry in this enumeration was opened at THIS head, and each says so where it is listed**:
this revision's own group (whose members are enumerated under it), the `#13124` eight, §2.5's five, and the
four EP/watermark addresses §2.1 and §2.7 carry. ⛔ **So read the entry, never this sentence, for those;
and an entry that names no head at all is a GAP in the enumeration, not a licence to read `ae54df166d`
into it.** ⛔ **There
is no rule for an address OUTSIDE it — open the line, never infer its head.** Such an address was *written*
at the pin `ccb49741b2ec`, and that is where
the census read it; it is not a promise that the line still carries what the row quotes. `#13083` found
sixteen written at the pin that never opened there, and the `#13124` review found eight **written at
`main`** and sitting outside this list — so **the enumeration is a list of what has been RE-DERIVED, not
the whole of the `main` set**, and the pin is the default for a cite in neither list only because that is
where it was written.
⛔ **THE `#13854` REVIEW FOUND THAT BASIS HALF-TRUE FOR THIS DOCUMENT, AND THIS EDITION IS THE OTHER
HALF — read it, because it changes what the number under each row means.** The review measured addresses
outside the enumeration whose value was the pin's PLUS this branch's own insert delta: they opened on
unrelated code at BOTH heads, because a delta is not a re-derivation. Every one of them was then re-taken
the way the review asked — by opening the line that carries the row's own quoted fragment, never by
applying an offset — but **where it opens is THIS head, not the pin**: an outside address here now names
the line that holds its fragment on the tree this document is written against, while the enumeration
above still names `main` `ae54df166d`. So the census below compares two bases **on purpose** — pin text
against this head's text, one line per cite — and the drift figure is exactly that comparison, not a claim
that the document is written at the pin. ⚠️ A reader who opens an outside address at `ccb49741b2ec` will
land on the wrong line; open it at the head `#13854` names. The alternative — repointing the whole
document back to pin addresses — is the per-row pass the review recommended as its own doc lane, and it is
NOT done here.
⛔ **Take the head from the list, never from the row.** §2.1's whole-node row carries one of each
(`record_serve.py:468` is in THIS revision's group above; the row's `projection/__init__.py` member is in neither list, so it is at the pin),
and reading a row's head off its neighbours is exactly how a cite gets opened on a line nobody read. The
ratio below is a **whole-document** measurement — every `path.py:NNNN` pair in this file, the `main`-set
addresses above included — so it is the count of how much of a document written at the pin has since
moved, not a count of that remainder alone. ⚠️ **An earlier form of this paragraph called the ratio the
measurement of the pin's own remainder — a label its own population contradicts**, because the addresses
this header assigns to `main` are counted in it. On any later head they
drift **by construction**: a `main` integration that inserts a line above a cited site moves every cite
below it, with no symbol having changed. ⚠️ **That drift is already measurable against `main`** —
**136 of this document's 234** `file:line` pairs do not open where the pin has them, against `main` `ae54df166d` — the same
head the enumeration above names, so the figure and the list describe one comparison rather than two
(counted as the
unique `path.py:NNNN` pairs in this file after resolving **every** written path to the module it names
(a bare `name.py:` is `tortoise/name.py` when that file exists, and the one file of that name
otherwise — `edges.py` is `tortoise/projection/edges.py`, `propagation.py` is
`tortoise/projection/propagation.py`; a **directory-qualified** path is the same rule with its prefix
restored, so `projection/__init__.py` is `tortoise/projection/__init__.py`; a path already written repo-relative
(`tests/…`, `tools/…`, `tortoise/…`) is resolved AS WRITTEN and never prefixed — **skipping that step is what
produced the `96 of 218` this header carried before the `#13124` review**, which reported **31 pairs as
naming "a file that does not exist at `ccb49741b2ec`" when in fact all 31 files exist there** and which
was itself measured false; a range cite counts once, by its **start** line), which dedupes the **345** as-written spellings to 234 — **111**
collisions — each pair compared as `git show <head>:<path> | sed -n '<N>p'` against the pin, text for
text; **all 221 comparable ones resolve at BOTH heads** — every written path resolves to a module that
exists at `ccb49741b2ec` — and **the thirteen that are NOT comparable are addresses written at a head past the pin's EOF** — seven of
them this document's `sdk.py` cites, four its `search_engine.py` ones, one `hosted_api.py`'s, and the last
(`mcp_server.py`'s) past the pin only because `main`'s `#14169` rewrote that file under it — each
named at the address this document writes for it: `sdk.py:57972`, `:58120` (the `entity_type="event"` FTS call), `:58336`/`:58338` (the session-detail
read's `proj.g.query(` call and its column list), `sdk.py:58987` (the `:Source` traversal), `:62020`/`:62040` (the `:Meta` read and
its `_config_reset_state` delegate), `search_engine.py`'s `:7170`/`:7201` (the operator-expansion leg's preview and count)
and `:7577`/`:7579` (the `aboutSubject` probe and its `:Event` sibling), `mcp_server.py`'s `:11590` (the `:Tag` traversal §2.5's row
quotes), and `hosted_api.py`'s `:51493` (the `s.actor_user_id = $uid`
condition) — every one of their written lines sits past the pin's copy of its file
(`git show ccb49741b2ec:tortoise/sdk.py | wc -l` -> `56415`; `…/search_engine.py` -> `7071`; `…/mcp_server.py` -> `11466`; `…/hosted_api.py` -> `49851`), so all thirteen
were re-derived at a head the pin predates. So the drift is **128 of the 221 comparable, plus eight of those thirteen pairs** (the
five that do not exist at `main` either are `sdk.py`'s `:62020`/`:62040`, `hosted_api.py`'s `:51493` and
`search_engine.py`'s `:7577`/`:7579`), and
the ratio below counts them rather than pretending it compares them. ⛔ **That is a fact about the drift, not a licence to
re-derive from a symbol.**
**The drift is 136 of those 234.** ⚠️ **These counts are a parsing decision, and this header states which
one it took**: the 234 counts unique `(resolved path, start line)` pairs over `path.py:NNNN` spellings
written as ONE token. The document also writes six pairs as two code spans
(`` `cache_projection.py` `:2485` ``, a `main`-set address listed above, plus
`` `sdk.py` `:2917` ``, `` `sdk.py` `:62020` ``, `` `sdk.py` `:62040` ``,
`` `projection/entities.py` `:5765` `` and the `#13124` enumeration's
`` `projection/entities.py` `:5824` ``); three of them are resolved but not counted by the one-token rule —
**three** of the six are ALSO written one-token (`` `sdk.py` `:62020` ``, its `:62040` sibling and
`` `projection/entities.py` `:5824` `` in row 13), so they are among the 234 already — and counting that form gives **237 pairs and 139 drifters**. So the
basis must travel with the figure — 136 and 139 are one measurement under two counting decisions, not two
findings. ⚠️ **An earlier form of
this paragraph sat in the MIDDLE of the sentence it qualifies**, between `103` and `collisions`, so the
sentence read "…to 211 — 103 … collisions —" around a full paragraph; its alternative basis then quoted a
numerator that could not move. Text inserted without re-reading the line it lands in is the same defect
one level down.
⚠️ **The pin promise was RE-CHECKED, not assumed, in the `#13083` follow-up — and it did not hold.**
Sixteen superseded line-addresses did not open on the line their row quotes at `ccb49741b2ec`. ⚠️ **The
unit of that number is the ADDRESS, not the reader** — the re-derivation commit `f4fad2d8b027` records an
`old -> new` mapping for each of the sixteen, so the figure is auditable there instead of remembered here.
That is also why sixteen addresses are FIFTEEN cites below: the subject probe's four addresses
(`:6711`, `:6710-6713`, `:6713`, `:6716-6729`) became three (`:7025-7028`, `:7028`, `:7031-7044`) — three of
the four map onto the range `:7025-7028`, the fourth being that range's `Event` leg — and the third is cited
elsewhere in this document too. They had been advanced
by a line delta rather than re-opened: the `search_engine.py` ones (`:3064`, `:4477-4481`, `:6352`) name
lines that hold something else at **every** recent `main` head — no commit ever carried the FTS call at
`:3064`, the walk at `:4477` or the operator leg at `:6352` — so the number was drifted forward, not
read off. All sixteen were re-derived at `main` (`ae54df166d`) — ⚠️ **so they do NOT open at the pin
above** — and each is named here by the cite it became, with its
file — `search_engine.py` FTS
`:3098`, walk `:4780-4784`, operator leg `:6667`, subject probe `:7025-7028` (plus its `Event` leg `:7028`, and
`subject_binding_available()` at `:7031-7044`), label scan `:4708-4710`, HNSW
`:4123`, label derivation `:3003`, index note `:3070`; `projection/__init__.py` `("Object", ["name"])`
`:6869`; `sdk.py`'s second `capture_redactions` writer `:2876` (on this tree `:2921`); `audit.py`'s carrier probe `:2452`/`:2463`;
and `mcp_server.py`'s `entity_type` passthrough `:5529` (on this tree `:5720`) — and the drift figure
above was re-measured afterwards. ⛔ **That enumeration is NOT the whole of the `main` set, and an earlier
form of this header was false for saying it was.** The `#13124` review found a group it did not name —
`cache_projection.py` `:2485`, `:2542`, `:2714`, `:2714-2715`, `:2724-2726`, `:2776`, `:3841` and
`projection/entities.py` `:5824` — **eight addresses that open at `main` and not at the pin** ⚠️ **and
they are listed at the head the PINS read — this branch's integration head, which
`git log -1 --format=%H -- docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md` names, because the commit
that wrote this sentence is rewritten by a rebase and an unreachable sha is the same head-bound figure
as a line number — NOT at `ae54df166d`.** `#13494` re-derived them ON the tree so that each one's
`content` could be pinned there; the `#13124` review had measured them at `ae54df166d`, which is why
the `ae54df166d` values this paragraph goes on to quote (`:2720` / `:2715`, `:3837` / `:3834`) are not
the addresses in this list. (measured,
`git show <head>:<path> | sed -n '<N>p'` at `ccb49741b2ec` vs `ae54df166d`: the pin's `:3735` is
`for prop in sorted(STORED_VECTOR_PROPS):` — the `MERGE (n:…) SET n += $props` this document quotes is the
pin's `:3732` — and that pair reads `:3837` / `:3834` at `main`, where the `cache_tier` arm is `:3735` (the
pin's `:3633`); the pin's `:2715` is `if k not in tail_denied})` — the `tail_denied = forbidden | {` this
document quotes is the pin's `:2710` — and `main` holds those two at `:2720` / `:2715`; and
`projection/entities.py` `:5824` is the `s.summary=coalesce($summary, s.summary, '')` write on this
tree (it was a COMMENT at the pin AND at `main`: the write sits at `:5598` at the pin and `:5646` at `main`) — ⛔ every number in this clause was taken with `grep -n` on the tree that names
it, never by applying a delta: the two states are DIFFERENT files at DIFFERENT offsets, so a single shift
cannot describe the pair, and one applied here is what turns a pin-side description into a false one).
Five of the eight are already asserted at `main` by `_PINS` in `tests/test_12184_shifted_cites.py`, and the
test beside this paragraph pins all eight to the content they quote. They are listed here now. ⚠️ **So the
enumeration is what has been RE-DERIVED, each entry carrying the head it was measured at — never a claim
that nothing else is at `main`** — and a range entry spans every line it covers.
⚠️ **This revision adds its own entries to it**: `projection/__init__.py`'s fulltext declarations
`:6974-6990` (the block), `:6984` (`Event`), `:6985` (`Subject`), `:6989` (`Object`), `:6990` (`Source`);
`record_serve.py:468` (`READ_POINT_BY_ID`); `sdk.py:37904`/`:37927` (the whole-node pair, still 23 apart);
`audit.py:817`; `mcp_server.py:5250`/`:8515`/`:11590`; `goal_judgement.py:1128`; and §2.1's own table, which carries
two of this class as well — `sdk.py:24647` and `search_engine.py:7201`, both open at this revision and neither
open at the pin — plus row 13's four writer addresses, which the `#13394` sweep found pinned at **this head** (⚠️ NOT at
`main` `ae54df166d`, where `hosted_api.py`'s `:23046` is `for ev in (payload.events or []):` and the commit
door's `MERGE (s:Source {url:$did})` is the integration head's line) but
absent from this list, so the rule above was sending a reader to the pin, where **all four** of these
addresses are a comment or a docstring line at their current spellings (`sdk.py`'s two included) and none is
the commit door — the `params={"eid": _eid, "oid": _oid},` statement an earlier form of this sentence
named is the pin's `:23046`, which is not one of them:
`sdk.py:22288` (the capture writer) with its read-back `:22411`, and `hosted_api.py:23501` (the commit
door's `MERGE (s:Source {url:$did})`) with `_execute_commit_writes` at `:23313`. ⚠️ **The `hosted_api.py`
pair is head-bound and has FIVE readings on record** — `:22301`/`:22113` at the pin, `:22919`/`:22731` at
`ae54df166d` (`#13394` was re-cut onto it — `9d20f74886`, whose own subject says so — and ⚠️ **not** the
head `#14059` wrote them on; the clause above names `ae54df166d` only to say these four addresses were NOT
pinned there, which is
why the row-13 note below still records this reading alongside the pin's), 23082 / 22894 as the `#14022`-rot repair `#14059` LEFT them on `main` — `32441c280d`, its SQUASH commit; that PR's own head is `57cb42f642` and is not on `main`, which is why the squash is the commit named here — 23503 / 23315 at the base this
branch was then built on (`72c104bcbb`), and 23501 / 23313 at the head this sentence is written at (`main`
`1b0db04894`, after `e4acf78705` moved them `−2`). **This edition took the last two**, and row 13's note
below repeats the pin's and `ae54df166d`'s — it does not carry the branch-base 23503 / 23315, which no
longer names the door's `MERGE` line or its `def` (it is `+2` into a BYTE-IDENTICAL region, so the door
did not change between those heads — only the offsets above it did), so this paragraph is the only place
that reading appears. ⛔ **Re-derive by CONTENT:
never reuse the delta.** The `+419` that all three of this
row's and row 15's sites take between that squash (`32441c280d`) and this one is **not** one insert: that
interval is **569 inserted and 59 deleted** lines in `tortoise/hosted_api.py`
(`git diff --numstat 32441c280d HEAD -- tortoise/hosted_api.py`), across **25** hunks — and the convention,
the pathspec and the endpoint all travel with that second figure, because
`git diff -U0 32441c280d HEAD -- tortoise/hosted_api.py | grep -c '^@@'` gives 25 while git's default `-U3`
gives 23 (and `--numstat` prints no hunk count at all; without the pathspec both commands describe the whole
repo instead — every file in the interval, not this row's — and the repo-wide totals are **deliberately not
quoted here**, because they depend on the commits you run the command at; re-run it rather than read a figure
here). `+419` of the growth sits above the three sites (20 hunks, largest net `+157`)
and `+91` below them, and at the same two heads a cite at old 10500 moved `+97` while one at 40000
moved `+499`. The three share a delta because **no hunk between them changes the count** — the cumulative
shift is `+419` for every old address from 21885 to 38986, and that is a BAND, not a property of the file — a lane
that applies `+419` to a different address lands on a line nobody read, which is the failure this header
exists to stop. What makes the row survive is narrower and is what the pin guard grades directly: the
door region a reader opens is **byte-identical** across these heads and no deleted line in it carries
`MERGE (s:Source`, `_execute_commit_writes` or `s.summary=$summary` — so the row's verdict, mechanism and
quoted fragments are untouched; and §2.5's `:Tag`
paragraph, whose **five** reader addresses the `#13879` review re-derived from that row's own quoted
fragments — the group this list carried only as the number it then gave for the `mcp_server.py`
member — `sdk.py`: `:31572`
(`MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag})`), `:31554` (the tag list with
counts — `MATCH (t:Tag)` + `OPTIONAL MATCH (p:Point)-[:TAGGED]->(t)`), `:24534` (the tag-name match —
its `WHERE t.name IN $names` filter is the line below it) and `:24878` (`MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t`, the
orphan GC), and `mcp_server.py`: `:11590` (`MATCH (t:Tag) OPTIONAL MATCH (t)<-[:TAGGED]-(q)`). The four
`sdk.py` ones are spelled as bare `:NNNN` siblings behind one path, as the `mcp_server.py` group above
does, and NOT as new `path.py:NNNN` pairs (a bare sibling is not counted at all — that is the point):
a pair spelling would
add five more **as-written spellings** — 345 → 350 — and so 111 → 116 collisions, since a collision is a
spelling that shares its `(path, line)` pair with another, while adding **no pair at all**: 234 either
way, because each of the five already carries its pair from §2.5. A bare sibling adds neither. The pin
agrees: `_REDEFINED_13854` pins each of these four by content at ONE occurrence of the
§2.5 token, so a second one-token spelling would red it. Each was
opened at this head, and ⛔ **none of them
opens at the pin**: at `ccb49741b2ec` the four `sdk.py` numbers
read an unrelated statement (`git show ccb49741b2ec:tortoise/sdk.py | sed -n '31551p;31533p;24513p;24857p'`),
and the fifth has **no line there at all** — `git show ccb49741b2ec:tortoise/mcp_server.py | wc -l` -> `11466`, so
`:11590` is past the pin's EOF, and the address that list carried at the pin was `:10730`, which IS that very
traversal (`git show ccb49741b2ec:tortoise/mcp_server.py | sed -n '10730p'` -> `OPTIONAL MATCH (t)<-[:TAGGED]-(q)`),
so the pin's row was right there and only the re-derived `:11281` this file carried until now was not — so the head these five belong to is this one, and a
reader sent to the pin would have found a different line at four of the five — which is why they are
enumerated here rather than left to the paragraph's own say-so. ⚠️ **And nine MORE addresses this revision writes at THIS head were in neither list — this PR's own review found them — so they are enumerated here now**: `live.py`: `:667` (the `EPISTEMIC_LABELS` tuple the EP walk is swept per label from; the pin's `:667` is a docstring line), `sdk.py`: `:15894` (the `MATCH (m:GraphEventMeta) RETURN m.first_seq` watermark arm; ⛔ the `:15873` this list carried is, at THIS head, the string `"event(s) are unaccounted for, so this poll cannot "` inside the `ChangeFeedDegraded` raise — and at the pin it is a comment line, `# is graded on the merged state like any addressed edit, so a` — so it names this arm at neither head, `#14183` final review), `hosted_backup.py`: `:844` (the `max(m.last_seq), max(m.first_seq)` read, the pin's line being an `if not rows` guard) and `:916` (the `MERGE (m:GraphEventMeta) SET …` restore writer, the pin's line being a bare `)`), and — from §2.6, whose row this revision re-pointed to this head — `hosted_backup.py:892` (the `max(e.seq), min(e.seq)` aggregate; the pin's `:892` is `max_seq, min_seq = (rows[0] if rows else (None, None))`), `hosted_backup.py:1333` and `hosted_backup.py:1534` (the two whole-graph RESTORE-side scans, the pin's `:1316` being `dropped_vectors += 1` and its `:1517` a blank line — the `retired_vectors` rename lands later, so at THIS head `:1316` reads `retired_vectors += 1`). and §6.3's `sdk.py`: `:16098-16103` (the `_emit_event` docstring's JSONL contract — ⛔ the `:16077-16082` this list carried is the docstring's call-styles list, and `:16077` at the pin is unrelated prose, so that range named no contract at either head, `#14183` final review) and `:3801` (the turn hard delete that emits `PointRetracted`; the pin's `:3801` is a different statement, `sdk._graph_write_with_retry(`). ⛔ **THREE of the nine are graded AT THIS SITE; the other six are not, and this sentence said so falsely
until it was measured.** The §2.6 `:892` row and the `:1333` / `:1534` pair each carry a one- or two-occurrence
count in `_TOKEN_OCCURRENCES`, so re-pointing one of them here REDS the suite (measured: `:1333`→`:1334`
→ 2 failed). The other six are written above in this document's two-code-span form (`sdk.py`: `:15873`)
while the pin rows carry the same address in their single-token spelling, and each such token is satisfied by a
sibling occurrence elsewhere in this file — so re-pointing **all six at once leaves the suite at 30
passed** (measured, with a control above proving the mutation is live). Do not read this enumeration as
guarded until those six have a site-level pin; the gap is the bare-`:NNNN`-code-span class recorded on issue #14089
(open: a wrong address in the "ten that are NOT comparable" enumeration above passed 24/24 — that
list is where the case was OBSERVED; the witness addresses the issue quotes (`:58265`, `:58272`, `:58274`,
`:58923`) appear nowhere in this document, so do not follow this pointer expecting to read them here), not
the census-row gap on #14204. ⛔ **The three `sdk.py`
numbers above are the ones the pin rows carry.** An earlier cut of this paragraph named
`:15787`/`:15958-15962`/`:3777` and called them the PIN-side numbers; measured at the pin `ccb49741b2ec`
that is false — `:15787` is an unrelated `#2199` comment, `:15958-15962` is `embed_compose` prose and
`:3777` is a blank line, while the three statements sit at `:13761`, `:13932` and `:3561`. The earlier
text's parenthetical ("the pin's `:15787` is a comment", "`:15958` is unrelated prose", "`:3777` is an
empty line") was accurate; the provenance narrative that replaced it was not. ⛔ **One class this revision writes is NOT head-relative, and is pinned anyway — do not read the sentence above as covering it.** The five addresses in this revision's new §2.2 `:Source` reader row and in row 13 — `session_source_prose_migration.py`: `:218-223` (the `_SOURCE_ROWS` census read `find_session_sources_carrying_prose` issues), `:1263-1268` (the `_HITS_ROWS` read — keyed on the urls the FTS leg RETURNED, so it is the follow-up to `run_fts_query` and not the FTS read itself), `:274-281` (the `_REPAIR_PAIR` block, a `SET` that READS the node) with its inner `:277-279` (the `AND coalesce(s.summary,'')=$old_sum … SET s.summary=$sum` pair row 13 quotes), and `:1292-1300` (`_CONTROL_LEXICON_ROWS`, the prefix predicate over exactly this episodic population) — **all open at the pin `ccb49741b2ec` exactly as they open here**, because `tortoise/session_source_prose_migration.py` is the SAME FILE at both heads (`git rev-parse ccb49741b2ec:tortoise/session_source_prose_migration.py HEAD:tortoise/session_source_prose_migration.py` → one blob, `77c86b24e5`). They need **no head declaration**: what was missing is the GUARD, not the head. They are named here once, only so a reader is not left to the default on a cite this revision wrote, and each is pinned by content in `tests/test_12184_shifted_cites.py` — because a cite that resolves at the pin is still UNPINNED, and the next merge that inserts into that file moves all five with nothing red. ⛔ **A `file:line` in NEITHER list has an UNDECLARED head: the pin
`ccb49741b2ec` is where the census USUALLY wrote it, not where it provably IS.** Open it there first, but
a hit at the pin is a hypothesis about the census's history, not a fact about the line. A review of this
PR found one address in exactly that position whose head is `main`: `goal_judgement.py:1128` is
`where = "MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator"` at `main`, and that statement is
the pin's `:1119` — so the cite was written at `main` and reading the pin's `:1128` (`# — deterministic,
and immune to being SILENTLY truncated…`) would look like a defect. It is enumerated above now. An
UNDECLARED head is resolved by OPENING it, never by inference. If the
line does not carry what the row quotes, re-derive the cite from the row's own quoted fragment
(`grep -n '<fragment>' <path>`) rather than moving its head by a guess. That is the enumeration the rule at
the top of this header sends the reader to.
⛔ **A LATER PASS FOUND 34 ADDRESSES OF THIS CLASS AND RE-DERIVED THEM BY CONTENT (`#13854`).** The
`#13854` review measured them delta-shifted — the address equals the pin's plus this branch's own insert
delta, so it opens on a comment or unrelated code at **BOTH** heads (`sdk.py:38873` is the pin's
source→source match, and the shifted `:38840` is ``last_pass``-prose at this head, where the statement is
now `:42431`). Twenty-five are the ones the review named; **two of its 27 do not appear in this document
at all** (`sdk.py` `:2917`/`:2924` and `:46612`/`:46644`), and the other nine are bare siblings in the same
rows — `:2705`, `_POINT_DENY`'s three consumer addresses (`:3184-3186`, `:3228`, `:3278`), and the
whole-node family's five. ⛔ **AND THE PASS IMMEDIATELY BEFORE THIS ONE MADE IT WORSE:** it applied a
blanket `-23` to eight `projection/entities.py` cites that were already correct at that head, so
`:1246`→`:1223` landed on a BLANK line and `:3880`→`:3857` on a module import — the offset-not-content
failure this header records, one level up, shipped as a correction. Every one of the 34 was re-taken here
with the row's own quoted fragment, and the figures below were re-measured afterwards.
`tests/test_12184_shifted_cites.py`'s `_REDEFINED_13854` now pins all 34 by content, so the next
offset-only edit reds instead of shipping.
⚠️ **The figure counts the file it is written in, so every revision moves it — and it is MEASURED over this file as it now stands, not carried forward**: the form this paragraph replaced quoted `111 of 217`. ⚠️ **It matched the editions up to the one that carried it — which is why it is recorded as provenance, not carried forward.** Measured by this header's own rule: at `43028d178b` (its drift counted at the `main` head the retired sentence names, `534f3e1bee` — **not** at `43028d178b` itself, where the same rule reads 126, and not at `534f3e1bee`'s own edition of this document, which reads `52 of 164`) the document is **314 spellings → 217 pairs → 111 drifters**, so the figure was RIGHT when written; at `295c26ebc8`, the edition that CARRIED it, the same rule gives **320 → 216 → 110**, so the sentence was already one edition stale on arrival. And `111` is a DRIFTER count, so comparing it against a pair count is not a re-measurement — the pairs at `43028d178b` are 217, which is exactly what the retired sentence said. The figures here are **345 written spellings → 234 pairs → 111 collisions → 136 drifters of the 234**, at `main` `ae54df166d`, re-measured against this file rather than carried forward by arithmetic from an earlier edition. ⚠️ **That re-measurement was performed at THIS edition, and the count this paragraph replaces was an EARLIER edition's, not the previous one's** — the edition immediately before this one (`7697a6b762`) already read `327`/`214`/`113`/`122` — `git show 7697a6b762:docs/architecture/CAPABILITY-PLACEMENT-AUDIT.md | grep -o 'The figures here are .*'`, so that number was **stale** by then, not a count of a different population: this file's own citation set is what the rule counts, so any revision that adds, moves or drops a cite changes it. That is why the figures were recomputed rather than adjusted, and ⛔ a `+N` on an earlier edition's count is exactly the offset-not-content failure recorded one paragraph above. ⛔ **A `+N` applied to a previous count is exactly the offset-not-content failure the paragraph above records, one level up**: the earlier form of this header read `98 + 10 = 108`, which was true of the file it was written for and false of this one. Every cite **this revision writes** was opened at the head it names before it was written; the ones it INHERITED were not re-opened, and the ratio above is the measurement of how many of those no longer land.
⛔ **An earlier draft of this paragraph asserted a population of "two"** and undercounted it by an order of
magnitude — the omission class §2.3 records — which is why that count is now MEASURED by
re-running the stated method over this file (the figures at the top of this header) instead of enumerated here.
⛔ **The `:3524` address in `sdk.py` that §2.4 retracts is NOT counted**, because it is a **comment line**: it is spelled
bare, not as a `path.py:NNNN` pair. A cite that cannot be opened on the line its row quotes is worse than
no cite: the handle beside it then reads as corroboration of a line nobody read.

⛔ **A later pass found the remaining species of the same defect: a PROXIMITY claim, not a line number.**
§2.5's whole-node row said the whole-node read sat *"three lines above the walk"* — both cites were
individually correct at the pin they were written for, and the distance is **23** (`MATCH (s:Subject {id:$sid})
RETURN properties(s)` and the walk `MATCH (s:Subject {id:$sid})-[r]->(n) …`, with the subject-chain composition
between them). ⚠️ **That row's two ADDRESSES have since been re-derived twice — they are `:37904` and
`:37927` now, both in this revision's own group above — and this paragraph is the record of the DISTANCE
claim, so it quotes no address the row does not carry**: a number
here that is not the number the row carries is the very defect it is warning about. So the rule above extends to the words
**around** a cite: an adjective that fixes a position ("above", "beside", "in the same read") is a claim too, and
it is checked the same way — by opening both lines at the declared head and counting. ⚠️ **That correction is
itself a record of a PARTIAL sweep**: it first claimed to cover this document's cites when it had covered three
sections' tables, and the next pass found more in the tables it had not opened. The claim "every cite was opened"
is only ever true of the pass that just ran, so the honest form is to name the pass and to expect the next reader
to find one.

Most rows name the writer beside the cite, or quote the Cypher on it, and that handle is what makes the line
re-derivable. ⛔ **No count of the rows that "carry a handle" is quoted here on purpose**: the figure turns
on whether a bare module name counts as one, and it has already been read three different ways (15, 14 and
13 of the 18 rows in §1). What IS checkable is the five **§1** rows carrying no re-findable handle at all: rows
10 (`residency.py:284`), 11 (`why_snapshot.py:484`) and 14 (`webhook_delivery.py:495`) give a number and
nothing else; row 15 gives bare module names beside `hosted_api.py:32965` and one bare `pack_manifest_store.py:467`
— neither cite a re-findable handle; and row 18 declares itself `found, NOT censused` — a GAP, see §7. Those
five are *at risk* of drifting silently: no symbol will be there to re-find — for them the number IS the
citation — so an insert above a cited site is undetectable from the row alone. The risk has already begun
to bite: of the line-numbered cites in these five rows, **ten no longer name the same line at `main`**
(re-measured at `main` `ae54df166d`, the head this header's own lists are written at, and RE-MEASURED again for this edition — the `111-of-217` this clause used to defer to has been retired) — row 15's `hosted_api.py:32965`, whose
`MERGE (p:PackInstall …)` is at `:33583` there (`:34276` at this head — `33853 + 419` put it at `:34272`, the SAME band row 13's pair takes, NOT a second one, and `#14169` then moved it a further `+4` (`33853 + 423 = 34276`; one band plus `#14169`'s own two hunks above the site — `8` added / `4` deleted — not two bands); re-derived by content at `main` `b4da1f5c01`), and **9 of row 18's 16** (that row's `hosted_api.py`, `sdk.py` and `pipeline.py` addresses moved; its seven `metering.py` / `cohort_cost.py` ones are unchanged) — while rows 10, 11 and 14 and row
15's `pack_manifest_store.py:467` still do. ⚠️ **The comparison this count is taken on is the PIN against `main`, never `main` against this head** — `git show ccb49741b2ec:tortoise/pipeline.py | sed -n '529p'` is `"k": pipe.pipeline_key, "kind": pipe.pipeline_key,`, while `ae54df166d` holds `"MATCH (p:Pipeline {pipelineKey:$k}) RETURN p.defaultStageId, "` on that line: it MOVED, which is what makes it the ninth of row 18's sixteen, even though `main` and this head agree on it.
⚠️ **Row 14 does NOT, and this clause is measured at `ae54df166d`, not at THIS head**: `63bc13edb44` — the +8 shift of `webhook_delivery.py` that §2.7 records — is not an ancestor of `ae54df166d` (`git merge-base --is-ancestor 63bc13edb44 ae54df166d` → 1), so at `ae54df166d` row 14's census address `:487` still opens on its quoted `CREATE`, while §2.7 re-derived it to `:495` for THIS head. Every other address this document carried into that file moved in the same pass, and §2.7's `:GraphEventMeta` row records the whole old→new mapping. (For rows 10, 11 and 14 the SHAPE's reader half is anchored — §2.7 names `_drop` and quotes the
Cypher — but the row's own WRITE cite is not.) Re-derive each cite from the text
its own row carries, which is the rule §9.1 of `STORAGE-ARCHITECTURE.md` applies to its own cites.
The **byte** figures do not drift this way: they are re-derivable from the committed JSON arms at any
head (§1.1, §4).

---

## 1. The population — enumerated from the WRITERS

The population is not the ontology's wish list; it is what a writer **actually puts on a node**. ⚠️ **The claim
is scoped, not 'everything in the database':** it covers the **memory substrate** (rows 1–6) plus every
**graph-only bookkeeping label reached from the code paths this audit read** (rows 7–17). It is **not** a census
of the registry / tenancy / metering substrate — and the labels found but NOT censused are listed in row 18
rather than silently omitted.

Two writers reach the graph, and both were read:

1. the **live tenant write path** (`sdk.*` + `projection/entities.py`'s folds), and
2. the **record → cache projection** (`cache_projection.py`, `NODE_TABLES` → 6 labels), which is what a
   rebuild materialises.

The inventory below is the union. `d` = the label's record table in `cache_projection.NODE_TABLES` (a record
HOME — ⚠️ **not a tier**: `cache_tier` is a column on `public.point` only, and `cache_projection.py:3841`
applies the tier predicate `if table == "point"`, so a `record_only` row-tier is mechanically expressible for
`:Point` alone and every other label needs its own migration — §5.3's third precondition); `—` = graph-only
bookkeeping with **no `NODE_TABLES` record table** (relevant in §5.2; ⚠️ the qualifier is load-bearing —
`:GraphEvent` has a stream table, `public.journal_event`, that is not a `NODE_TABLES` row — §6.3).

| # | shape (label) | written by | record table | node count in the measured corpus |
|---|---|---|---|---|
| 1 | `:Point` (claims, operators, turns) | `sdk.create_point`/`update_point`, `projection/entities._upsert_point_props`, `projection/propagation` | `public.point` | 66 (60 turn points + claims) |
| 2 | `:Source` (documents, transcripts) | `sdk._create_source`, `_upsert_source`, `_upsert_document`, `sdk._materialize_session_source` | `public.source` | 2 |
| 3 | `:Event` (lifecycle, AgentSession) | `sdk.create_event`, `_upsert_event`, `hosted_api` commit path | `public.event` | 3 |
| 4 | `:Session` | `sdk._write_session_and_turns`, `projection/entities.py:3982`, `hosted_api:16304` | `public.session` | 7 |
| 5 | `:Object` | `sdk.create_object`/`create_entity`, `_upsert_object` | `public.object` | 2 |
| 6 | `:Subject` | `sdk.create_subject`, `_upsert_subject` | `public.subject` | 2 |
| 7 | `:GraphEvent` (change feed) | `event_store.append_event` (`sdk._emit_event`) | **—** | 67 |
| 8 | `:GraphEventMeta` (seq allocator) | `event_store.next_seq` | — | 1 |
| 9 | `:EpMeta` (EP version) | `sdk._advance_ep_version` | — | 1 |
| 10 | `:Meta` (residency declaration) | `residency.py:284` | — | 0 (arm not exercised) |
| 11 | `:WhySnapshot` | `why_snapshot.py:484` | — | 0 (arm not exercised) |
| 12 | `:Tag` | `projection/edges.py:1881` (`MERGE (t:Tag {name:$tag})`, reached from `sdk`'s tag sync) | — | 0 (arm not exercised) |
| 13 | `:CommitRecord` (idempotency) | `commit_idempotency.py:45` (`_MERGE_CYPHER`), `:65` (`_UPDATE_CYPHER`, the `status` transition) | — | 0 (not exercised) |
| 14 | `:DeliveryReceipt` (webhook) | `webhook_delivery.py:495` | — | 0 (not exercised) |
| 15 | `:Batch`, `:PipelineStage`, `:PackInstall`, `:PackSelection`, `:PackManifest` | `mining.py`, `pipeline.py`, `pack_state.py`, `hosted_api.py:32965`, `pack_manifest_store.py:467` | — | 0 (not exercised) |
| 16 | `:ContextSubscription` (webhook registration) | `webhook_delivery.py:414` (`CREATE (s:ContextSubscription {…})`) | — | 0 (not exercised) |
| 17 | `:OnboardingState` / `:OnboardingStep` | `onboarding/state.py:317` (`MERGE (n:OnboardingState {org_id})`), `:322`/`:929` (`MERGE (s:OnboardingStep {org_id, step_id})`), `:324`/`:934` (`MERGE (n)-[:COMPLETED_STEP]->(s)`) | — (`COMPLETED_STEP` is an EDGE type, not a record table — `NODE_TABLES` has six labels and onboarding is not one of them) | 0 (not exercised) |
| 18 | **found, NOT censused** — `:Pipeline` (`pipeline.py:529`), `:MeteringRecord` (`metering.py:108`, `:840`, `:1146`, `:1608`), `:MeteringUnmeteredIncrement` (`metering.py:616`), `:WebhookEvent` (`hosted_api.py:48880`), the tenancy/registry labels `:Team` (`sdk.py:50607`), `:Membership` (`:51271`), `:APIKey` (`:51039`), `:TeamMeta` (`:50604`), `:Graph` (`:50686`), `:SignupToken` (`hosted_api.py:35990`), `:Invitation` (`hosted_api.py:31144`), **`:Organization`** (the metering twin's label — read by `metering.py:304` and `cohort_cost.py:301`, both `MATCH (o) WHERE (o:Organization OR o:Team) AND …`; ⚠️ **it was missing from this list until the `#13095` review**, so the "found" set was itself incomplete), and the **retired** `:Document` | sdk / metering / pipeline / migration | — | **not censused — a GAP, see §7** |

⚠️ **`:Document` is in this row because it is a MIGRATION STATE, not a live shape — and its readers are still
named, so nothing is hidden behind the omission.** Post-D10 a document **is** a `:Source`
(`projection/edges.py:834-843`), so the label only exists on a graph that has not yet run the relabel. Its only
readers are that migration's own named-property read (`migrate_kinds.py:296-300`, `MATCH
(n:{_LEGACY_DOCUMENT_LABEL}) RETURN ID(n), n.id, n.url, …` — capability **`none`**) and its pre-write re-scan of
the same label joined to `:Source` (`migrate_kinds.py:310`, `MATCH (n:{_LEGACY_DOCUMENT_LABEL}:Source) RETURN ID(n)`, `_LEGACY_DOCUMENT_LABEL` = `"Document"` at `:89`) and the deprecated-alias
acceptance at `projection/edges.py:2965-2966`; its only writer is the relabel itself (`migrate_kinds.py:430-439`,
`SET n:Source … REMOVE n:Document`) — `:297`/`:296-300` is its ENUMERATOR, a reader, named two sentences above and
deliberately not repeated here as a writer. **No placement verdict is available on it**: the
label is removed by the migration, so "what may be resident" is not a question that has an answer — the population
question is "has this graph been relabelled yet", which is `#5140`'s. ⛔ An earlier draft gave it its own censused
row (the first `:Document` row, immediately before this one) while giving it no §2 reader census and no §3 verdict,
which made the population statement claim a census it never received.

⛔ **Row 18 is a real gap, labelled as one.** This audit read the memory-substrate writers and the bookkeeping
labels on their paths; it did **not** classify the billing / tenancy / pipeline substrate. **No placement
decision on `:Pipeline` or `:MeteringRecord` may be made from this document.** (`:Pipeline` is named
explicitly because the same module writes the censused `:PipelineStage`.)

Rows 10–17 are declared **not exercised by the instrument** rather than counted as zero-cost — a shape
the corpus does not create is a shape this document has *not* measured, and saying so is the difference
between a census and a guess.

### 1.1 The instrument

`docs/evidence/13083-capability-census/probe_shape_bytes.py` — a fixed, synthetic corpus (deterministic
ids, texts and 384-dim vectors), seeded through the **production chokepoints** (`sdk._write_session_and_turns`,
`sdk._capture_session`, the `sdk.create_*` doors, and `cache_projection.record_plan` for the projection
arm). Bytes are the server's own `INFO memory → used_memory` (the instrument §9.1's own measurement uses,
`docs/evidence/10000-turns-bytes/README.md`) plus per-property figures summed in Python as `len(str(value).encode())` and emitted as `repr_bytes_by_label`
— a **RENDERED** size, because `size()` refuses a scalar and returns an ELEMENT COUNT for a list, so no
single Cypher spelling yields stored bytes for every property type.

**Its limits are quoted, not discovered later** — `used_memory` includes block/index matrices and its
baseline drifts between runs; and the per-property column is a **rendered** size: exact for a string, but an
**over-report of ~4.7×** for a numeric array (a 384-dim float vector renders as ~7,187 characters; its stored
`float32` payload is 1,536 B). §4 shows both columns and derives an array's footprint from its width.

---

## 2. The reader census — every reader at `file:line`, by capability

Each row is a **reader**, not a mention — and a `SET` expression that READS the node counts as a reader. The
capability column is what the reader *needs*; a reader that needs none of the five is SQL-shaped and is
marked **none**.

⚠️ **One rule decides most of the property-level judgement calls below, and it is stated in full once — §2.4's
`sibling rule`, immediately after the `:Session` table.** In one line: a property's verdict comes from how its
READER uses it (a predicate **inside** a walk, or an answer **composed** with one), never from the query it shares a
`RETURN` with. Rows 2b–2f of §3 are classified by it.

### 2.1 `:Point`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/projection/propagation.py:81`, `:86` | `MATCH (n:Point {id:$id})-[r:IMPL]-(m:Point)` / `-[r:NAND]-(m:Point)` — a BFS EP blend that is **capability-carrying in form and DEAD as evidence**: both lines sit inside `_compute_confidence` (`def` `:58`), whose only caller is `propagate_shock` (`:10`), and the method's own docstring says so (`:72` — *"It is UNREACHABLE today (no production caller; `validation`'s e2e test asserts `propagate_shock` is never invoked)"*). `tests/test_bfs_audit.py` is that audit, kept green as a test | **(none — dead path)** |
| `tortoise/ep.py:1620-1632`, swept once per label from `live.py:667` (`EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")`) | `MATCH (a:{label}) WHERE a.id IN $ids WITH * MATCH (a)-[r:IMPL\|NAND]-(b)` — **the LIVE belief walk**. ⚠️ `{label}` is why a `:Point` grep never found it: ONE call site reads four labels, and `ep.py` is the file the EP engine actually walks | **EP state + adjacency** |
| `tortoise/sdk.py:41749` | `MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL … RETURN n.id, n.ep_alpha, n.ep_beta` — an anchored label scan plus a property filter returning three scalar columns. It reads stored EP state but **walks nothing**; under §2's own rule (lines 29–33: a filtered listing is SQL-shaped) it is `none`. ⚠️ An earlier draft filed it as capability `EP state`, which counted a listing as a walk | **none** |
| `tortoise/sdk.py:18056` | `MATCH (p:Point {id:$id})-[:extractedFrom]->` — provenance walk to the Source anchor | **traversal** |
| `tortoise/mcp_server.py:11158` / `:11161` | `MATCH (new:Point {id:$n})-[r]->(t)` / `(t)-[r]->(new:Point)` — edge discovery | **adjacency** |
| `tortoise/projection/edges.py:1888` | `(p:Point {id:$pid})-[:TAGGED]->(t:Tag)` | **traversal** |
| `tortoise/sdk.py:24647` | `(n:Point {id:$id})-[:TAGGED]->()` | **traversal** |
| `tortoise/search_engine.py:4950-4954` | `MATCH path = (seed)-[:IMPL\|NAND*1..{max_hops}]-(n:Point)` — `expand_structural_hops`, a **variable-length** belief walk (min hop distance) | **traversal** |
| `tortoise/search_engine.py:7170` | `(op)-[r2:IMPL\|hasPart]-(other:Point)` — the operator-expansion leg's subquery PREVIEW | **adjacency** |
| `tortoise/search_engine.py:7201` | `(op)-[r2:IMPL\|NAND\|hasPart]-(other:Point)` — the operator-expansion leg's COUNT, the statement that follows `:7167` inside the same function | **adjacency** |
| `tortoise/search_engine.py:7577` | `(n:Point)-[r:aboutSubject]->(:Subject)` (inside `_SUBJECT_SOURCE_SCOPED_PROBE`, `:7576-7579`, consumed only by `subject_binding_available()` at `:7582-7595`) — an **edge-existence probe** answering "can this graph carry `aboutSubject` edges at all", not a per-node degree prior; it fails OPEN | **adjacency** |
| `tortoise/search_engine.py:3157` | `CALL db.idx.fulltext.queryNodes('Point', $query)` | **fulltext index** |
| `tortoise/search_engine.py:4878-4880` | `run_structural_query`'s `MATCH (n:{label}) WHERE …` — a plain label scan, no pattern | **none** |
| `tortoise/search_engine.py:4293` | `CALL db.idx.vector.queryNodes('Point', 'embedding', $limit, vecf32($query_vec))` — the HNSW leg | **vector index** |
| `tortoise/domain_validators.py:105` (and the same property-join shape at `:174-182`, `:197-205`) | `MATCH (req) WHERE (req:Point OR req:Object) AND (req.pointKind IN $req_kinds OR req.objectKind IN $req_kinds)`, then `OPTIONAL MATCH (wp:Point {wf_id: req.enabled_workflow})` — **property-equality joins only**; no relationship appears in any pattern | **none** |
| `tortoise/domain_validators.py:153-158` (`orphan_use_case`) | `OPTIONAL MATCH (uc)<-[:hasPart]-(op:Point {is_operator:true, op_type:'composedOf'})-[:hasPart]->(jtbd:Point)` — a **2-hop `hasPart` walk**, i.e. every parented use-case is found THROUGH the tree | **adjacency** |
| `tortoise/domain_validators.py:241-243` | `MATCH (n:Point {status:'draft'}) WHERE n.is_operator = false AND NOT (n)--()` — the `orphaned_draft` rule (#131) tests **existence of any edge**, which is adjacency | **adjacency** |
| **the whole-node accessor family** — `tortoise/sdk.py:27821` (`_promote_point`), `:28720` (`approve_merge`), `:30378` (`_query`), `:30474` (`_paginated_query`), `:30573` (`_get_point`'s `_cache_answer`) and `:44202`/`:44209` (`_diary_read`'s wing/no-wing pair); `tortoise/record_serve.py:468` (`READ_POINT_BY_ID`, `status=SERVED`); `tortoise/projection/__init__.py:14647` (`_rebuild_all_replay`'s `MATCH (n:Point) RETURN properties(n)`; ⛔ the `:14169` this row carried is `_replay_log`'s `sspm.reconcile_after_replay(...)` call — not a whole-node read, `#14183` review round 3); and the label-generic `tortoise/record_reconcile.py:533` | `MATCH (n:Point …) RETURN properties(n)` — the **whole node**, handed back as a dict. ⚠️ **A whole-node read IS a reader in this census** (§2.4 counts it for `capture_redactions`, row 14 for `Point.mentions`), and an earlier draft omitted the whole family from this table while counting it elsewhere — which is how the `story_arc` "zero readers" error survived three drafts | **none** |

**Verdict: `graph`.** Eighteen reader rows — **thirteen capability-carrying**, **four deliberately `none`**
(`none`: `search_engine.py:4878-4880`, a plain label scan; `domain_validators.py:105`, a property-equality
join; `sdk.py:41749`, a filtered EP listing; and the whole-node accessor family above), and **one row (two lines)
that is capability-carrying in form and dead as evidence** (`propagation.py:81`/`:86`). The thirteen span **six files**
(`ep.py`, `sdk.py`, `mcp_server.py`, `projection/edges.py`, `search_engine.py`, `domain_validators.py`) — the
four `none` rows bring `record_serve.py`, `projection/__init__.py` and `record_reconcile.py` as well; a
record-backed copy could not answer any of them without re-implementing belief
propagation, the **variable-length hop walk**, the fulltext index or HNSW in SQL (which is `#13082` Phase 2 and its stop condition).

⚠️ **The two rows this table used to cite for EP were DEAD CODE, and that is how the live walk stayed missed.**
An earlier revision presented `propagation.py:81`/`:86` as *"EP propagation over belief edges"* — the shape's
strongest citation, and its only EP-**walk** one. Both are inside `_compute_confidence`, which has no production caller
(the method says so itself, `:72`; `tests/test_bfs_audit.py` is the audit of exactly that). The **live** walk
is `ep.py:1620-1632`, driven by `EPISTEMIC_LABELS` (`live.py:667`) — label-parametric over **four** labels,
which is why a `:Point` grep never found it. The verdict never rested on the dead pair (row 2's `graph`
verdict is the walk, and the walk is live), but the evidence did, and a census whose only EP-**walk** citation
is dead code is the failure mode this document exists to prevent. The dead pair is **named rather than
deleted**, so this correction stays visible.

⚠️ **A correction this census needed against itself.** An earlier draft cited
`domain_validators.py:106`–`:244` as one "kind filters, no walk" row and quoted a query
(`MATCH (req:Point) …`) that **does not exist** — the code reads `MATCH (req) WHERE (req:Point OR
req:Object)`. The range also contains `:241-243`, whose `NOT (n)--()` is an **adjacency** predicate, which
is the shape's own second capability — and `:153-158`, whose two-hop `hasPart` pattern is a third: the
first correction of this row split it only two ways and still filed `orphan_use_case` as `none`. All three
are fixed above (three rows, not two): the cite is split, and the mis-quoted text is gone. The error was
invisible to a reader who did not open the file, which is exactly the failure this document exists to
prevent — recorded rather than quietly edited.

### 2.2 `:Source`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/sdk.py:42386` | `MATCH (s:Source)-[:references]->(o:Source)` — source-to-source provenance | **traversal** |
| `tortoise/sdk.py:58987` | `MATCH (s:Source {url:$url})-[:references]->(n)` | **traversal**. |
| `tortoise/sdk.py:38207` (target side) | `(p:Point)-[:extractedFrom]->(s:Source)` — every claim must reach its source (§9.4 ①) | **traversal** |
| `tortoise/search_engine.py:3157` | `CALL db.idx.fulltext.queryNodes('<label>', $query)` — the index `:3129`'s note says is built on **`Source._searchText` for every Source** | **fulltext index** |
| `tortoise/session_source_migration.py:432-434` | `SET new.provenance_spans = CASE WHEN coalesce(size(new.provenance_spans),0)=0 THEN $spans ELSE new.provenance_spans END` — a **`SET` that READS the node**, i.e. a reader by §2's rule | **none** |
| `tortoise/session_source_prose_migration.py:218-223`, `:1263-1268`, `:274-281` | the episodic-Source **prose** reconciler, the same shape one file over: `_SOURCE_ROWS`/`_HITS_ROWS` are keyed `RETURN … s.summary …` reads, and `_REPAIR_PAIR`'s `AND coalesce(s.summary,'')=$old_sum … SET s.summary=$sum` is a **`SET` that READS the node**. Not test-only and not dead: `reconcile_after_replay` runs it on every replay engine's trailing sweep, and on the rebuild, backup-restore and consistency paths. Capability `none` — property projections, a prefix predicate and a keep-arm — so this row adds a reader without moving a verdict; row 13 (§3) carries the placement verdict for `Source.summary` itself, and ⚠️ this row is also why that row's absence claim was false before this revision | **none** |

⚠️ **That last row is the rule that put it here.** §2's method statement says a `SET` expression that reads
the node is a reader. `provenance_spans` is this audit's own instance of that — and it was **missing from an earlier
draft**, which is the more instructive failure: a `SET`-that-reads is exactly what a `MATCH`-only grep walks
past. Capability `none` (a property guard: it fills an absent value and never walks), so the verdict stands.

**Verdict: `graph`.** The link is load-bearing: §9.4 makes "every claim walks up to its Source" a
mechanism, not a citation nicety.

> ⚠️ **The fulltext index is declared on FIVE labels, not two.** This is the census's most easily missed
> reader: `_FULLTEXT_INDEX_LABEL_FIELDS` (`projection/__init__.py:6974-6990`) declares `Point`, `Event`,
> `Subject`, `Object` and `Source`, and the query is **label-parametric** (`search_engine.py:3062` derives
> the label from `entity_type`), so ONE call site serves all five. The FTS row is carried for `:Point`
> (§2.1) and `:Source` (§2.2) *and* for `:Event` (§2.3) and `:Object`/`:Subject` (§2.5); leaving it out of
> the last three made them look adjacency-only. No verdict flips — all five are `graph` either way — but an
> incomplete enumeration is exactly the defect a census exists to prevent.

### 2.3 `:Event`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/goal_judgement.py:1128` | `MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator` | **adjacency** |
| `tortoise/search_engine.py:7579` | `(m:Event)-[r2:aboutSubject]->(:Subject)` | **adjacency** |
| `tortoise/assembly.py:2632` | `MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event)` | **adjacency** |
| `tortoise/search_engine.py:3157` | `CALL db.idx.fulltext.queryNodes('Event', $query)` — the label-parametric FTS leg (`:3062` derives the label from `entity_type`; `sdk.py:58120` passes `"event"`); the index declares `("Event", ["subject","name"])` at `projection/__init__.py:6984` | **fulltext index** |

**Verdict: `graph`.** ⚠️ **Whole-node reads are absent from the table above and belong to it by §2's own rule —
and this is a LOWER BOUND of eight, not a census of two.** The two the earlier draft found are
`tortoise/session_indexer.py:746` (`MATCH (e:Event {eventId: $eid}) RETURN properties(e)`) and the
label-generic `tortoise/record_reconcile.py:533` (`MATCH (n:{label}) RETURN properties(n)`); cycle-3 review then
found **six more in `sdk.py` alone** — `:40752` (`MATCH (e:Event {eventId:$eid}) RETURN properties(e)`),
`:41271` (`MATCH (e:Event) RETURN properties(e) ORDER BY e.startedAt DESC LIMIT 20`), `:52042`
(`… WHERE e.eventId IN $ids …`), `:52143` (`… {eventKind: $ek} …`), `:52149` (unfiltered, `LIMIT $lim`) and
`:53744` — every one a whole-node `:Event` read in product code. ⛔ **The count is stated as a lower bound on
purpose**: the earlier draft asserted completeness for exactly two, which is the same omission class §2.1
records against itself for `:Point` ("which is how the `story_arc` 'zero readers' error survived three
drafts"), and the check is `git grep -n 'properties(e)\|properties(n)' -- 'tortoise/*.py'` — the second
pattern for the label-generic `MATCH (n:{label}) RETURN properties(n)` class `record_reconcile.py:533` belongs
to — not another review. All eight are
capability **`none`**, so they add no capability and change no verdict — they are named so that the census's
"each row is a reader" claim is true rather than merely convenient.

### 2.4 `:Session`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/hosted_api.py:24770-24802` (`list_sessions`, `:24673`) | the **session LIST** endpoint — the same `CONTAINS` walk as the detail reads, with the row counts returned beside it: `OPTIONAL MATCH (s)-[:CONTAINS]->(p:Point)` (`:24772`) `WHERE (p.pointKind IS NULL OR p.pointKind <> 'event')`, then `RETURN s.id, s.created_at, s.turn_count, count(p), s.actor_user_id, s.harness` (`:24789-24790`), `s.machine_id, s.model` (`:24791`), `s.capture_ok, s.capture_extractor` (`:24797`), `s.client_captured_at, s.client_captured_at_source, s.capture_lane` (`:24801`) — `count(p)` IS the per-session extraction count this endpoint exists to report. ⚠️ **It was missing from this table until the `#13095` review**, which is why the sibling rule below is written down rather than left to the reader. (Re-measured for the delivering revision: at `6c1a01df87` the same query is `hosted_api.py:24513-24544`, handler `:24415`.) | **adjacency** |
| `tortoise/hosted_api.py:24356`, `:24453`, `:24572`, `:25847`, `:25865` | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point)` — session detail and its turn reads | **adjacency** |
| `tortoise/sdk.py:3132`, `:3716`, `:57941`, `:58007`, `:58064` | the same `CONTAINS` walk from the SDK door — `:3155` the turn-id split, `:3716` the stale-turn sweep (`… WHERE t.id IN $ids DETACH DELETE t`), `:58007` the turn read (`{pointKind:'event'}`, hosted's `:45098` twin), `:57941`/`:58064` the two `(p:Point)` walks. ⚠️ An earlier revision cited `:3524` in this cell, which is a **comment line** inside the `#3947/#10017` block — the cite opened, the code did not | **adjacency** |
| `tortoise/session_reinjection.py:561` | `MATCH (s:Session)-[:CONTAINS]->(seed)` — multi-session evidence pooling; ⚠️ the SAME statement carries a second `CONTAINS` walk (`:568-570`) whose membership the `is_episodic` turn-shape filter decides (`_TURN_SHAPE_FILTER`, `:268`), so this cite is adjacency for `:Session` and rule 1 for `:Point.is_episodic` (row 2b) | **adjacency** |
| `tortoise/activation_scorecard.py:395` | `(s:Session)-[:CONTAINS]->(p:Point)` | **adjacency** |
| `tortoise/session_schema.py:855-862`, `:892`, `:907-917` (`audit_session_retrofit`, reached by the shipped CLI `tools/session_retrofit_audit.py`) | the short-turns query counts `stored_turns` through `OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point)` before returning `s.capture_ok, s.capture_extractor, s.harness, s.capture_lane` — **four of the seven expressions** in that RETURN (`s.id, s.turn_count, stored_turns` precede them), so the guard against reading it as a two-property projection is "four of seven", not "four, not two"; `WHERE s.capture_ok = true RETURN count(s)`; and the entity-link under-count, `OPTIONAL MATCH (s)-[r1:aboutObject]->()` + `(s)-[:CONTAINS]->(t:Point)-[r2:aboutObject]->()`, `WHERE live_edges > s.entity_links_created` | **adjacency** |
| `tortoise/quota.py:1155` | `MATCH (s:Session) RETURN count(s)` | **none** |

⚠️ **The sibling rule — stated ONCE here, because the table above now carries three kinds of property read and
they do NOT share a verdict.** `list_sessions` is the clearest case in the census: one `count(p)` and twelve property
columns in a single `RETURN`. What separates them:

1. **Inside the walk** — a predicate evaluated on the nodes the traversal visits, so it decides the walk's
   MEMBERSHIP. `t.is_episodic = true` and `t.pointKind = 'event'` in the two `session_schema.py` walks and in
   `_capture_turn_ids` (`sdk.py:3134`), and `(p.pointKind IS NULL OR p.pointKind <> 'event')` in `list_sessions`
   itself (`hosted_api.py:24788`). These are **`graph`**: change them and the walk visits different nodes.
   ⚠️ **Three of this rule's members are cited above for ADJACENCY only, or not at all**, so the rule is
   stated over the walks rather than over the rows, and row 2b counts all eight: the two capture retry-**heal**
   scans (`sdk.py:20041-20044`, `hosted_api.py:21194-21196` — `coalesce(n.is_episodic, false) <> true`, a
   READ-then-STAMP whose matched ids are written back at `sdk.py:20049` / `hosted_api.py:21201`) and the C4
   turn-shape filter (`session_reinjection.py:568-570`, `_TURN_SHAPE_FILTER` at `:268` = `coalesce(p.is_episodic,
   false) = true AND …`, inside the `CONTAINS` walk the table above cites at `:561`). ⛔ Earlier revisions of this
   section and of row 2b said "five … walks" as if the set were closed; it was the set *the census had happened
   to cite*, which is the same defect class as the `story_arc` "zero readers" claim one property over.
2. **Composed with the walk** — the returned ANSWER is a function of BOTH the walk and the property:
   `stored_turns < s.turn_count`, `live_edges > s.entity_links_created`, `max(total, mark + 1)`. **`graph`**: the
   figure cannot be produced by either side alone, so no store can hold the answer without also holding the walk.
3. **A sibling column** — `RETURN s.capture_ok` beside `count(p)`, or the same property projected out of a pure
   point lookup. The property's value does not depend on the walk, so its own capability is **`none`**: it reads
   identically off a record row. Adjacency sharing a statement is not adjacency the property needs.

⚠️ **A sibling column does NOT carry the verdict — and the code says so in its own words.** `session_schema.py:852-853`: *"`capture_ok` is reported beside it so a reader can see the class the epic named
without a second query"* — *reported beside it* IS the definition. The decisive case is `sdk.py:58338-58360`, the
session-detail read, which is a **pure point lookup** (`MATCH (s:Session {id:$sid}) RETURN …`) with **no walk in
the statement at all** and still returns `machine_id`, `model`, `capture_ok`, `capture_extractor`,
`client_captured_at`, `client_captured_at_source`, `capture_lane` **and** `turn_max_index`: a property's
capability cannot turn on a walk that is not there, which is why `turn_max_index`'s `graph` verdict rests on the
COMPOSED site (`hosted_api.py:45028-45040`) and not on this one. ⛔ Applied consistently below: the four sibling
members of verdict row 7 move into row 7b, and **no label's verdict changes** — four *properties* change row,
which is the whole point of a census that classifies properties rather than statements. ⚠️ The rule cuts inside a
`graph` row too: `harness` is a sibling projection in BOTH session reads (`sdk.py:58338`, `hosted_api.py:24790`) and
is `none`-capability — row 6 is `graph` for its `CONTAINS` structure and `turn_count`'s composition, never for
`harness`.

**Verdict: `graph`.** ⚠️ Its **property families are another matter, and the family SPLITS — by how each
property participates in a walk, not by which query happens to mention it.**

- **`graph`, because the answer is COMPOSED with the walk — and may not move:** `turn_count` (the retrofit's
  `WHERE stored_turns < s.turn_count` compares the property to a walk-derived count), `entity_links_created`
  (`live_edges > s.entity_links_created`, `live_edges` summed from two `aboutObject` walks) and
  `turn_max_index` (below). A `public.session` row cannot reproduce any of the three, however plain the
  projection looks — the figure is a function of BOTH the property and the walk.
- **`none`, because they are only REPORTED BESIDE a walk — property-level `record_only`, row 7b:**
  `capture_ok`, `capture_extractor`, `capture_lane`, `entity_links_attempted`. ⛔ **This reverses an earlier
  revision of this section, and the reversal is recorded rather than quietly made**: that revision filed them
  `graph` "because the query returns them from a walk", which is the same defect the `turn_max_index`
  correction below had to fix one property over — the property's value does not depend on the walk, so no
  capability is needed to read it (§2.4's sibling rule).

**`turn_max_index` is `graph`**, by the composed rule and one query family over — but the justification is
**single-site**, and an earlier draft of this paragraph claimed two. At `hosted_api.py:45028-45040` the
returned answer IS composed with the walk: `total = int(nodes or 0)` then `total = max(total, mark + 1)`,
the anti-phantom prior count. At `sdk.py:4399-4406` the walk supplies a **sibling column** —
`_prior_mark_index(_prior[0][0], _prior[0][1])` takes only `turn_max_index` and `turn_count`, and
`collect(t.id)` is consumed by a different variable (`_split_graph_turn_ids`) — so that site alone does not
carry the mark's answer, and citing it as if it did was wrong. One site is enough under the rule that the
`graph` column is not a scale of agreement.
⚠️ An earlier draft said these properties had "no Cypher reader of their own", and its §2.4 table omitted
`session_schema.py` entirely, which is why the falsifying readers went unseen; §6.1 records the correction.
The **remainder** (`capture_redactions`, `capture_ok`, `capture_extractor`, `capture_lane`, `harness`, `actor_user_id`, `machine_id`,
`model`, `client_captured_at*`, `value_nodes_*`, `draft_count`, `commit_count`, `entity_links_attempted`) is a
MIXED set, and precision here is the point — it is not one claim:

- **Read by Cypher, `none` capability:** `capture_ok`/`capture_extractor`/`capture_lane` are the **sibling
  columns** of two walks — the retrofit audit's short-turns query (`session_schema.py:855-861`, whose own
  comment at `:852-853` says they are *"reported beside it"*) and `list_sessions`
  (`hosted_api.py:24770-24802`) — and of the session-detail **point lookup** (`sdk.py:58338-58360`, no walk in
  the statement at all); **`actor_user_id`** is a sibling in that same LIST row (`hosted_api.py:24790`) and in the
  session-detail endpoint's OWN point lookup, which has no `CONTAINS` walk either
  (`hosted_api.py:25612-25630`, `s.turn_count, s.actor_user_id, s.harness,` at `:25614`), a filter
  in the **LIST** endpoint's own conditions (`hosted_api.py:51493`, `s.actor_user_id = $uid`) and carries its own `:Session` index
  (`projection/__init__.py:21185`) — sibling, filter, index, and no walk, so `none` like its neighbours;
  `entity_links_attempted` is a sibling column of the entity-link walk
  (`session_schema.py:916-917`); `value_nodes_created` and `value_nodes_held` are BOTH named by the commit-path
  state load (`hosted_api.py:22307-22308`), but only `value_nodes_created` is incremented by a `SET` that reads it
  inside the commit atom (`hosted_api.py:22980-22981`) — `value_nodes_held` is incremented by its OWN `:Session`
  `MERGE`/`SET` at `hosted_api.py:24493-24497`, a statement with no `:CommitRecord` in it; `machine_id`, `model`, `client_captured_at`,
  `client_captured_at_source` are named by the session-detail read (`sdk.py:58338-58360`), with
  `client_captured_at` also read inside a `SET` (`sdk.py:4265-4268`); **`draft_count` and `commit_count`**
  are read inside the same commit-statement `SET` (`hosted_api.py:22982-22985`) — §2's reader rule, exactly
  as `provenance_spans` is counted in §2.2, and an earlier revision of this paragraph omitted them.
- **No shipped query NAMES it — but it IS read back as a WHOLE NODE:** `capture_redactions`. `sdk.py:2898` and
  `:2921` only WRITE it (`SET s.capture_redactions=$redactions`) and no product `RETURN`/`WHERE` names the
  property — the one Cypher read that does name it is a TEST assertion
  (`tests/test_capture_secret_redaction_4911.py:250-254`), which is not a PRODUCT read — but two
  whole-node reads carry it, and a whole-node read is a reader by this document's own vocabulary
  (row 14 counts `sdk.py:30140`'s `get_point` — *"Returns dict of all properties"* — as one). `tools/backfill_copy_turns.py:188-192`
  is `RETURN id(s), s.id, properties(s)`, and its own comment at `:177-183` names this key explicitly as one
  the record must not lose (*"a null `capture_redactions` is a MEANINGFUL value … dropping the key would make
  the record unable to falsify that reading"*); `projection/__init__.py:14484` is `MATCH (s:Session) RETURN
  properties(s)`, the pre-wipe snapshot restored by a whole-schema writer (`session_schema.py:493-505`). Its
  named-COLUMN reader is the Postgres record (`record_read.py:1001`). ⚠️ An earlier revision of this bullet
  said "the graph has never read this count back" — false, and the same error class this section is written
  against, which is why it is corrected rather than reworded away.

Every Cypher site above is a point lookup, a property guard or a **sibling column** — capability **`none`** —
so `record_only` (row 7b) stands on the CAPABILITY rule, not on their being unread; a **property-level**
question listed in §6, not silently folded into this row verdict. ⚠️ One site changes what §6.1 can ask: the
`value_nodes_created`/`draft_count`/`commit_count` increments live in the **same statement** as the Session
`MERGE` (`hosted_api.py:22973-22989`), so no `public.session` row can join that atom — ⚠️ but
**`value_nodes_held` is NOT one of them**: it is incremented by a standalone `:Session` `MERGE`/`SET` at
`hosted_api.py:24493-24497` — `MERGE (s:Session {id:$sid}) SET …, s.value_nodes_held = coalesce(s.value_nodes_held, 0) + $n, …`
with no `:CommitRecord` in the statement. So the **`:CommitRecord`-atom** argument does not reach it, while the
Session-`MERGE` coupling §6.1 defines does — which is why it needs its own placement ruling rather than the
counter's: the read is
`none`-capability, but the WRITE is graph-coupled, which is a live input to the placement ruling.

### 2.5 `:Object` and `:Subject`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/assembly.py:2632` | `(o:Object)<-[:aboutObject]-(e:Event)` | **adjacency** |
| `tortoise/audit.py:817` (and `:2452`, `:2463`) | `OPTIONAL MATCH (carrier:Object) WHERE carrier.name = sup.supersededBy AND id(carrier) <> id(sup)` — **no relationship in the pattern**; a property-equality scan | **none** |
| `tortoise/definite_description_backfill.py:434` | `MATCH (o:Object) OPTIONAL MATCH (o)<-[r]-() WITH o, count(r) …` — degree census | **adjacency** |
| `tortoise/sdk.py:37927` | `MATCH (s:Subject {id:$sid})-[r]->(n)` | **adjacency** |
| **the whole-node accessor family** — `tortoise/sdk.py:37904` (`_provenance`: `MATCH (s:Subject {id:$sid}) RETURN properties(s)`, and the walk it pairs with is **23 lines below** at `:37927`, not beside it — the subject-chain composition sits between them), `tortoise/onboarding/seed.py:267` (the same read, from the seeder) and the label-generic `tortoise/record_reconcile.py:533` (`MATCH (n:{label}) RETURN properties(n)`, which reads `:Object`/`:Subject` too) | the **whole node**, handed back as a dict — the class §2.1 gives its own row, and §2's rule (stated in §2.3 for `:Event`) makes it this table's obligation too | **none** |
| `tortoise/search_engine.py:3157` | `CALL db.idx.fulltext.queryNodes('<Object or Subject>', $query)` — the same label-parametric leg (`entity_type="object"` at `assembly.py:2118`, `aggregate.py:547`, `coverage_loop.py:345`; no **product** caller passes the literal `'subject'` (test modules do — `tests/test_search_engine_gaps.py:1395`, `:1559`, `tests/test_integration_search.py`) — `mcp_server.py:4368` is a tool DOCSTRING, and the leg rides the caller-supplied value through the passthroughs at `mcp_server.py:5250`, `:5720`, `:8515`); the index declares `("Subject", ["name"])` at `projection/__init__.py:6985` and `("Object", ["name"])` at `:6989` | **fulltext index** |

`:Tag` belongs on this row too, and an earlier draft got it **wrong** by calling it bookkeeping: every tag
read resolves the node **through the edge** — `sdk.py:31572`
(`MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) RETURN properties(p)`),
`sdk.py:31554` (`MATCH (t:Tag) OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) …` — the tag list with counts),
`sdk.py:24534` (`MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) WHERE t.name IN $names`) /
`mcp_server.py:11590` (`MATCH (t:Tag) OPTIONAL MATCH (t)<-[:TAGGED]-(q)`), and
`sdk.py:24878` (`MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t`, the orphan GC) — all five
addresses opened at this head and re-checked in this pass (#13879 review, P2): the `mcp_server.py`
one was stale at main's head, at `:11280` → `:11281` (`#13991`), and main's own `sdk.py`
insertions (`e0accb8fe6`, `#13994`) then moved the four `sdk.py` addresses by `+31` — each re-derived by the fragment it
quotes (`grep -nF`), never by applying that delta — while the `mcp_server.py` one stayed where
`#13991` left it only down to `#14169`: `b4da1f5c01` then moved it a further `+309`, so the address
`#13991` corrected is now HISTORY and the line quoted above is the one this head carries — the
provenance below is that record, not a second cite. ⚠️ **The correction did not arrive on `main` from this pass.** This pass
re-derived the address itself (commit `143b1fea07`, 12:26Z) on parent `1f7ae03e`, where this document
still carried the stale address and not the corrected one; **#13992** (`8747965ad5`) put the same
address on `main` at 16:14Z, 63 minutes before this pass merged, sweeping a second site — the header
trio — that this pass's one-line edit did not. A later merge brought `main`'s copy in, so the address
value here is `main`'s and only the wider quote in the line above is this pass's. A later reader should
treat the address as done and not re-attribute the correction to this pass. A traversal needs its
**endpoint in the graph**, so "the node is only the endpoint label" is not an exemption — it is the
justification. **Verdict: `graph`.**

**Verdict: `graph`** for both.

### 2.6 `:GraphEvent` — the shape with no graph-capability reader

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/event_store.py:991-993` (`read_after`, whose `def` is `:922`) | `e.seq > $after` **ordered range scan**, optional type filter, `LIMIT`/dedupe | **none** |
| `tortoise/event_store.py:645`, `:1105` | `:645` is `MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)` (both aggregates); `:1105` is the `min(e.seq)` half of the `:1103-1110` RMW | **none** |
| `tortoise/event_store.py:801`, `:879` | per-page key existences inside the emit path | **none** |
| `tortoise/event_store.py:1043` | `MATCH (n:GraphEvent) RETURN count(n)` — the overflow path's whole-label total, counted BEFORE `purge_overflow` (`:1038`) decides the excess. An aggregate, not a key existence and not a delete; the two purge deletes are `:1029`/`:1053`, on the row below | **none** |
| `tortoise/event_store.py:1029` (`purge_expired`) / `:1053` (`purge_overflow`) | predicate deletes — `WHERE n.ts < $cutoff` and `ORDER BY n.seq ASC LIMIT $overflow` + `DETACH DELETE` (the **def** lines are `:1014`/`:1038`) | **none** |
| `tortoise/hosted_backup.py:892`, `:1333`, `:1534` | `:892` is `MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)`; `:1333`/`:1534` are whole-graph RESTORE-side scans (`MATCH (n) RETURN labels(n), properties(n)`) — aggregates. ⚠️ **These were `:890`/`:1182`/`:1383` when the census merged**: `hosted_backup.py` drifted in **THREE offset steps, and no single offset is right** — measured with `diff -U0` between the pin `ccb49741b2ec` and this head: `+2` (one added line after the pin's `:58`, one after its `:59`), `+60` after the pin's `:1035` (`diff -U0` → `@@ -1035,0 +1038,60 @@`), and `+89` after the pin's `:1175` (`@@ -1175,0 +1238,89 @@`) — cumulative `+2`, `+62`, `+151`. So `:1182` and `:1383` both land `+151`, at `:1333`/`:1534`, while the naive `+134` that an earlier draft applied lands at `:1316`/`:1517` — **and those are NOT the statements**: at this head `:1316` is `retired_vectors += 1` and `:1517` is `len(unlinkable), missing_endpoints,` (the PIN's `:1517` is the blank line). The statements are re-derived BY CONTENT rather than shifted by a constant, which is why this row cites `:1333`/`:1534` and not the `+134` arithmetic. Re-derived at this head, the same file-scoped treatment §2.7 gives `webhook_delivery.py`. ⚠️ **Three sites named `:GraphEventMeta` were removed from this table, and the rule that removed them is stated here because nothing else in it states one**: a statement whose pattern names only the SIBLING label is a reader of that label, not of `:GraphEvent`, so it belongs to §2.7's `:GraphEventMeta` row and not to this one — `hosted_backup.py:844` (`MATCH (m:GraphEventMeta) RETURN max(m.last_seq), max(m.first_seq)`; the census's `:842`, `+2` above it) and, for the identical shape, `event_store.py:557` / `:657` (`MATCH (m:GraphEventMeta) RETURN max(m.last_seq)`), all three of which §2.7 cites. Earlier drafts attributed `hosted_backup.py:842`'s columns to this row and left `event_store.py:557`/`:657` here beside it, which is why the rule is written down rather than implied. ⛔ **The one statement that IS censused in both tables is `event_store.py:1103-1110`** — cited here by its `:1105` `min(e.seq)` line and in §2.7 by its `:1103` start, because `MATCH (m:GraphEventMeta) OPTIONAL MATCH (e:GraphEvent) … min(e.seq) … SET m.first_seq` reads BOTH labels: it is an RMW on `:GraphEventMeta` whose floor is computed from `:GraphEvent`. Its `:1103` address belongs to §2.7 and its `:1105` to this row, which is the distinction the two tables draw. | **none** |
| `tortoise/sdk.py:15878` (the `read_after` call inside `poll_events`, `:15754`) | pages the feed | **none** |
| `tortoise/why_snapshot.py:526` | pages the feed to rebuild a why-snapshot | **none** |
| `tortoise/webhook_delivery.py:1049` (`event_head_seq`) | `max(e.seq)` — aggregate | **none** |
| `tortoise/webhook_delivery.py:1308` | `read_after(proj, int(sub.get("cursor") …))` — the subscriber delivery page | **none** |

**Verdict: `record_only`.** Not one traversal, not one adjacency, not the fulltext index, not EP state,
not a vector. This is the single largest remaining graph resident on the record-armed lane: §9.1's own
measurement puts `:GraphEvent` at **801 B/turn** — the *entire* 32,032 B/session residue
(`docs/evidence/10000-turns-bytes/README.md`). `docs/plans/2026-11-15-issue-12894-storage-shape.md` §A2.6
reached the same finding from the other direction ("whether those carriers should leave the graph is
explicitly *a placement decision of their own*") and did not claim it; **this document claims it.**

⚠️ **It cannot be moved by §9.1's mechanism, and §5.2 says exactly why.**

### 2.7 The graph-only bookkeeping labels

| shape | readers (`file:line`) | capability |
|---|---|---|
| `:GraphEventMeta` | `event_store.py:557`, `:657`; `hosted_backup.py:844` (`MATCH (m:GraphEventMeta) RETURN max(m.last_seq), max(m.first_seq)`); `sdk.py:15894` (`MATCH (m:GraphEventMeta) RETURN m.first_seq` — the 410/expired watermark arm of the feed, NOT `:15771` (which is `_decode_cursor`'s `return seq`) nor `:15868` (`raise ChangeFeedDegraded(`, whose message names no node)); `webhook_delivery.py:1060` (the head fallback — ⚠️ this cite was `:1052` when the census merged and the code has since drifted +8; re-derived here); `event_store.py:491`/`:738` are the `MERGE`s and `event_store.py:1103` a `MATCH … SET m.first_seq` **RMW** that advance or restore the watermark; and `hosted_backup.py:916` (`MERGE (m:GraphEventMeta) SET m.last_seq = $last, m.first_seq = $first`) is the **restore** writer, named here because §2.6's rule points at this row as its home and an earlier pass left it out of both (⚠️ written with the file on every cite — a bare `:491` after `webhook_delivery.py:1060` lands in THAT file, inside `_write_receipt`'s signature (`def` `:490`), NOT on `event_store.py:491`'s `MERGE (m:GraphEventMeta)`; and ⚠️ **`webhook_delivery.py` itself has drifted +8 lines since the census merged** — `63bc13edb44` is the only commit to touch it, net `10 insertions, 2 deletions`, both above line 236 — so the addresses THIS ROW carries into it are re-derived at this head: the census's `:1052` head read is `:1060`, its `:738` deadline line is `:746`, and its `:1103` docstring line is `:1111`. ⛔ **The same drift made EVERY `webhook_delivery.py` cite in this document stale by exactly +8**; all of them were re-derived in the same pass — §1's `:406`→`:414` and `:487`→`:495`; §2.6's `:1041`→`:1049` and `:1300`→`:1308`; §2.7's `:425`→`:433`, `:435`→`:443`, `:444`→`:452`, `:452`→`:460`, `:461`→`:469`, `:475`→`:483`, `:487`→`:495`, `:502`→`:510`, `:1113`→`:1121`, `:1134-1136`→`:1142-1144` — because a file-scoped offset is exactly the kind of thing that is either fixed everywhere or trusted nowhere. The addresses on the LEFT are the census's and no longer land at this head; the ones the document now cites are on the right. ⚠️ **`hosted_backup.py` has drifted too, and NOT by a single offset**: `:842`→`:844`, `:890`→`:892` and `:914`→`:916` are **`+2`** (two import lines, after the pin's `:58` and `:59`), while `:1182`/`:1383` are **`+151`** to this head's `:1333`/`:1534` (measured by content, not by adding a block's worth of lines; the naive `+134` lands at `:1316`/`:1517`, which the §2.6 row records as NOT the statements) — so a single delta is right only for an address **below** the pin's `:1035` (the last line before the `+60` hunk, so only the `+2` imports sit above it) and wrong for every one **above** it, which is why §2.6 carries the head addresses explicitly rather than a shift) | **none** |
| `:EpMeta` | `sdk.py:36146`; `ep.py:473` | **none** |
| `:Meta` | `residency.py:244`, `:292`; `sdk.py:62020` (reads); `residency.py:298` is a `DETACH DELETE` scoped by an IDENTITY key only (`MATCH (n:Meta {key:$key})`) — see the criterion note under this table. ⚠️ **LOWER BOUND, not a census**: three more live sites — a WHOLE-NODE read `MATCH (n:Meta {key:$key}) RETURN properties(n)` at `projection/__init__.py:2004` (the body of `read_config_reset`, def `:1996`, beside the `sdk.py` `:62020` read this row already carries (written as two code spans: the same address the slot above names); ⚠️ the SDK delegate `sdk.py:62040` (`_config_reset_state`) is NOT a door here — `git grep '_config_reset_state'` at this head returns the `def` and no caller), an existence probe `MATCH (m:Meta {key:'event_fts_v2'}) RETURN 1` at `projection/__init__.py:21990`, and a SECOND identity-key-scoped `DETACH DELETE` at `projection/__init__.py:2042` (the same shape as `residency.py:298`) | **none** |
| `:WhySnapshot` | `why_snapshot.py:237` (read); `:303-304` is `_drop` — `MATCH (w:WhySnapshot) WHERE w.point_id IN $ids DELETE w`, ORPHAN cleanup scoped by IDS THE CALLER ALREADY HOLDS (not retention, and not time-scoped); `:350-351` is the retention prune — `WHERE w.assembled_at < $cutoff WITH w LIMIT 5000 DELETE w` (time-scoped) | **none** |
| `:CommitRecord` | `hosted_api.py:22973-22989` (the live commit path's read — it consumes `x.value_nodes_declared` as a predicate); `commit_idempotency.py:57` (read), `:65` — `MATCH … SET r.status=$status RETURN r.session_id, r.status, r.write_ops_billed, r.written_at, r.value_nodes_declared`, a **read-modify-write** (`CommitRecordStore.update` consumes all five returned columns, so `RMW` is the right word — an earlier draft called it read-free, which was wrong), and `:194` — the idempotency `DELETE`, whose `WHERE r.status = 'partial' AND coalesce(r.write_ops_billed, 0) = 0 AND r.value_nodes_declared IS NULL` **consumes three node properties as predicates**, so by this table's own criterion it is a reader (an earlier draft called it "not a read"); `hosted_api.py:22436-22437` (`MATCH (r:CommitRecord …)` then `SET r.telemetry=$t, r.budget_warn=$warn`) and `:24592-24593` (`SET r.write_ops_billed=1`, a LITERAL — not a parameter) have **no `RETURN`** and assign parameters/literals, so they read nothing of the node and are WRITERS (§1 row 13). ⚠️ Three drafts of this row called writers readers, then over-corrected `commit_idempotency.py:65` to read-free, then called `:194` read-free; the table now separates read / RMW / predicate-consuming DELETE / writer, site by site | **none** |
| `:DeliveryReceipt` | `webhook_delivery.py:483` (`RETURN properties(r)`); `:510` is the `MAX_RECEIPTS` `DETACH DELETE`, `:495` the CREATE — see the criterion note under this table | **none** |
| `:Batch` | `mining.py:94`, `:178`; `projection/__init__.py:14295` | **none** |
| `:PipelineStage` | `pipeline.py:349` (read); `:685`/`:731`/`:754-756` are `SET` statements — writes with no read of the node, not `RMW` and not reads | **none** |
| `:PackManifest` | `pack_manifest_store.py:524` (`MATCH (m:PackManifest) RETURN m.namespace, …`), `:539` (`RETURN m.namespace, m.yaml`), `export.py:133` (`MATCH (m:PackManifest {namespace:$ns}) RETURN m.yaml`); `pack_manifest_store.py:550` is the `DELETE`, `:467` the writer named in §1 | **none** |
| `:PackInstall` / `:PackSelection` | `export.py:126` (`:PackInstall`); `pack_state.py:878-881` (`MATCH (s:PackSelection) RETURN s.revision, s.configured_at, s.updated_by`); `projection/__init__.py:2335`, `:13801-13802`, `:17586-17587` (`MATCH (s:{_PACK_SELECTION_LABEL}) RETURN properties(s)` — **whole-node reads**, invisible to a `:PackSelection` grep because the label is the constant at `:2256`, the same interpolation that hid `:PackManifest`) | **none** |
| `:ContextSubscription` | `webhook_delivery.py:433`, `:443`, `:452`, `:460`, `:1121`, `:1142-1144` (the due-subscription read); `why_snapshot.py:557` (read). ⚠️ The `CREATE` and the `DETACH DELETE` named in §1 are both in **`webhook_delivery.py`** — `:414` and `:469` — not in `why_snapshot.py`, whose `:406` is `ids.append(pid)` and whose `:461` is a comment; neither file's writer is a reader | **none** |
| `:OnboardingState` / `:OnboardingStep` | `onboarding/state.py:759-763` (`MATCH (n:OnboardingState {org_id:$org_id})-[:COMPLETED_STEP]->(s:OnboardingStep) RETURN s.step_id`) | **adjacency** → **`graph`** (§3 row 11c) — listed here only so the label is not missing from the table |

**Verdict `record_only` for every row above except `:OnboardingState`/`:OnboardingStep` (which is `graph`)** — that is **12 record-only labels across 11 rows**, `:PackInstall`+`:PackSelection` sharing one.
Each row cites readers; where a line is a **read-modify-write** it is labelled `RMW` rather than presented as
a pure read, and the writer itself is the one named in §1. ⚠️ Two labels were moved OUT of this table by
review: `:Tag` (`graph` — see §2.5) and the onboarding pair (`graph` — row above). §5.2's blocker is
recorded for the rest — none has a record table, so none
can be expressed as a tier today. They are cheap *individually* (1–2 nodes per graph) and this document
does not pretend otherwise: they are listed because the census is complete, not because they are a lever.

⚠️ **The criterion for a predicate `DELETE`, stated once so the rows above stop looking inconsistent.** A
`DELETE`/`DETACH DELETE` is a **reader** when its `WHERE` (or its `MATCH` map) has to evaluate **node content** —
`commit_idempotency.py:194`'s `r.status = 'partial' AND coalesce(r.write_ops_billed,0)=0 AND
r.value_nodes_declared IS NULL` is the model — and it is **not** a reader when it is scoped by an **identity key or
a clock** the caller already holds (`residency.py:298`'s `{key:$key}`, `why_snapshot.py:303-304`'s
`point_id IN $ids`, `why_snapshot.py:350-351`'s `assembled_at < $cutoff` + `LIMIT 5000`). §2.6's
`event_store.py:1029`/`:1053` are listed as readers because `:1053` **orders by `n.seq`** (a property the statement
must read to choose its victims) even though `:1029`'s `ts < $cutoff` does not — so that row is the one genuinely
borderline case, and it is capability `none` either way. ⛔ **Every `webhook_delivery.py` address this header re-derives is enumerated at this head and NONE of them is content-pinned** — file-scoped drift in that file has already bitten once (the `+8` above), so this is a stated GAP (`#14054`), not a silent exemption: its cites are written as BARE `:NNNN` siblings (`:495`, `:433`, `:452`, `:483`, `:510`, `:1121` … — note `:487`, the census's pre-drift address, is NOT one of them: it is `return [r[0] for r in rows]` at this head, and every `:487` in this document is the left-hand side of the `:487`→`:495` mapping, not a cite) whose numbers also occur for other files in this document, so pinning them needs context-bearing tokens rather than the bare form. ⚠️ **`webhook_delivery.py:510-512` is the test of the rule,
not an exception to it**: `MATCH (r:DeliveryReceipt {sub_id:$sid}) WITH r ORDER BY r.ts DESC, r.receipt_id DESC SKIP
$keep DETACH DELETE r` **does** choose its victims by two node properties — the very test that makes `event_store.py:1053` a
reader — and it is exempt only because its `MATCH` map pins the subscription identity. What it does NOT contain is
a `LIMIT`; `SKIP $keep` keeps the newest `$keep` and deletes the rest, which an earlier draft of this note
got backwards. Four drafts read these sites as a mix of "reader" and "not a read" because the rule was never
written down; it is written down here, with its own hardest case named.

---

## 3. The verdict table

`needs graph?` = does **at least one** reader need one of the five capabilities. `verdict` follows the
issue's rule: **no such reader ⇒ `record_only`, however graph-native it looks.**

| # | shape | readers (summary) | needs graph? | verdict | one-line reason |
|---|---|---|---|---|---|
| 1 | `:Point` core (`id`, content, `pointKind`, `status`) | §2.1, 18 reader rows (13 capability-carrying + 4 `none` + 1 named-but-dead row) | **yes** — EP walk, `IMPL`/`NAND`/`TAGGED`/`extractedFrom` adjacency, FTS, HNSW — **all five** capabilities | **graph** | the belief walk *is* the product; **all five** capabilities read it |
| 2 | `:Point.ep_*`, `baseline_set`, `confidence` | **the live walk**: `ep.py:1620-1632`, swept per label from `live.py:667` (`EPISTEMIC_LABELS`). ⚠️ **Not** `projection/propagation.py:81,86` — dead (`_compute_confidence` has no production caller) — and **not** `sdk.py:41749`, a filtered listing with no walk, which §2.1 now files `none` | **yes** — EP state | **graph** | there is no EP in SQL without building one |
| 2b | `:Point.is_episodic` (the turn discriminator) | **a predicate INSIDE eight `CONTAINS` walks** — `sdk.py:3132-3135` (`_capture_turn_ids`), `:4404` (the `collect(t.id)` prior read, `:4368-4406`), `session_schema.py:857` (short turns) and `:912` (entity-link under-count), `hosted_api.py:45032` (the hosted prior turn count), and three walks the tables above cite for adjacency only or not at all: the two capture retry-**heal** scans `sdk.py:20162-20165` (`_capture_session`) and `hosted_api.py:21195-21196` (`coalesce(n.is_episodic, false) <> true`, READ-then-STAMP — the ids the predicate keeps are stamped back at `sdk.py:20170` / `hosted_api.py:21207`) and the C4 turn-shape filter `session_reinjection.py:568-570` (`_TURN_SHAPE_FILTER` at `:268` = `coalesce(p.is_episodic, false) = true AND …`, inside the `(s)-[:CONTAINS]->(p:Point)` walk). ⚠️ **Three label scans read the same property with NO walk at all** — `cache_eviction.py:229` (`POPULATION_RESIDENT_COUNT`'s `turn` population), `projection/__init__.py:13760` (the PRE-wipe episodic `:Point` roster, which fails closed rather than degrade), `quota.py:1307-1309` (the quota count's `(n:Point AND (n.is_episodic IS NULL OR n.is_episodic = false))` arm, beside the `:Object`/`:Subject` and `:Event` arms) — and each is **`none`** capability: a label scan filtered on a property is the same SQL shape that makes row 2e's sweeps `record_only`. The row's **`graph`** verdict rests on the eight walks alone, never on these; they are listed because "in no row at all" is this census's own named failure class, and these three were in no row until this revision | **yes** — it decides which members each walk counts | **graph** | every one of those walks visits a different set, and returns a different count, if this predicate moves — §2.4's rule 1, applied to a property rather than a label. ⛔ **`eight` is a LOWER BOUND, and the row says so on purpose**: three consecutive revisions said three, then five, then eight, and **the verdict needs only ONE site** — the count's job is to stop a lane reading the row as exhaustive. The two misses were the **negative** arms (`<> true` at the two heals) and a predicate spliced in by string concatenation (`session_reinjection.py`); the check that finds the rest is `git grep -n 'is_episodic' -- 'tortoise/*.py'` read for a `CONTAINS` in the same statement, not another review cycle |
| 2c | `:Point.speaker` | `sdk.py:45169` — `RETURN n.id, ev.startedAt, n.speaker, t.speaker, …`, a **sibling column** of the ask-annotation query's `OPTIONAL MATCH (n)<-[:CONTAINS]-(s:Session)` leg (whose answer is `collect(DISTINCT s.created_at)`); written at `sdk.py:2869` (`t.speaker=turn.speaker`); no `WHERE` names it anywhere | **no** — `none` capability | **`record_only`** (property-level) | a per-point string read straight off the node; the walk beside it collects session timestamps, not speakers — §2.4's sibling rule |
| 2d | `:Point.content_hash` | the dedup **`MATCH` maps** — `sdk.py:32433` / `:32395` / `:43479` (`_find_point_by_content`, `:43416`; a PRODUCT reader, called from `hosted_api.py:22529` and `sdk.py:21152`) — `MATCH (n:Point {content_hash:$ch})`, plus `:32527-32528` and `:32566` (`n.content_hash IN $hashes … RETURN n.content_hash, …`); plus the same-statement prior read `sdk.py:2855` (`WITH t, turn, t.content_hash AS prior_ch`) that feeds the vector-preserve `CASE` | **no** — property-equality lookups and a prior-value comparison | **`record_only`** (property-level) | an equality `MATCH` is the SQL-shaped reader the rule names. ⚠️ It IS indexed — `_POINT_RANGE_INDEX_PROPS` (`projection/__init__.py:6918-6921`) carries `content_hash` beside `id`/`pointKind` — but a **range index is not one of the five capabilities**: it is the b-tree every SQL store also has, which is why the index makes the lookup fast without making it graph-shaped. No FTS or vector leg declares it, and no pattern varies on it |
| 2e | `:Point.ep_dirty` / `ep_dirty_at` (the EP dirty queue) | `sdk.py:39338` (`MATCH (n:Point) WHERE n.ep_dirty = true … RETURN n.id` — a label scan), `:38619-38622` and `:39400-39402` (read-modify-write: `WITH n, (… n.ep_dirty_at <= $run_ep) AS retired_now SET n.ep_dirty = CASE WHEN retired_now …`) | **no** — label scan, property predicates, RMW | **`record_only`** (property-level) | ⚠️ the flag TRIGGERS the EP belief walk but is never read THROUGH one: the sweeps filter a label scan and then call the walk, which reads `ep_*` (row 2) — the flag itself needs none of the five |
| 2f | `:Point.sourceVersionTransit` (the `extractedFrom` read-version transit) | **no Cypher READS it — but Cypher WRITES it, so "unread" is not the reason for the verdict.** Written by Cypher at `sdk.py:18443` (the `_create_map` entry the live `CREATE` carries), `sdk.py:43424` and `projection/entities.py:3177` (both `SET n.sourceVersionTransit=$sv`, idempotent on replay), and wiped by the recreate rule at `projection/__init__.py:15496` (`n.sourceVersionTransit = NULL`); its VALUE is derived at write time by `api.py:2615-2618` (`_source_version_transit(resolve_source_versions(…))`), whose own read is a **point lookup** of the Source (`projection/edges.py:929` → `MATCH (s:Source {url:$url}) RETURN s.contentHash`). What reads the STORED key is the journal/replay path and the whole-node reads of §2.1 | **no** — `none` capability | **`record_only`** (property-level) | its derivation reads `contentHash` by URL equality, not by a walk; the transit is a payload convenience so pass-2 need not re-read the Source. ⛔ **The verdict rests on CAPABILITY, never on "unread"** — the same distinction row 13 and `story_arc` needed, and an earlier revision of this row got it wrong in exactly that direction |
| 3 | `:Point.embedding` | vector leg (HNSW) | **yes** — vector index | **graph** | the index lives in the engine (`STORAGE-ARCHITECTURE.md` §12.1) |
| 4 | `:Source` identity (+ `_searchText` on a **non**-episodic / post-`#10000` Source — the rows below split the episodic case out) | §2.2 | **yes** — `references`/`extractedFrom`, FTS | **graph** | §9.4 ① makes the link load-bearing |
| 5 | `:Event` core | §2.3 | **yes** — `aboutObject`/`aboutSubject` adjacency **and a fulltext index on `subject`/`name`** | **graph** | lifecycle edges are how status folds, and `name`/`subject` are FTS fields |
| 5b | `:Event.project` (a passthrough property) | **no Cypher names it** — written through the Event passthrough (`projection/entities.py:2068-2076` records the measurement: `create_event(…, project="kept-228")` lands it while `story_arc` is denied), read back only by the `:Event` **whole-node** reads — at least the **eight** §2.3 names (`session_indexer.py:746`, `record_reconcile.py:533`, and the six in `sdk.py` at `:40752`, `:41271`, `:52042`, `:52143`, `:52149`, `:53744`) | **no** — `none` capability | **`record_only`** (property-level) | the same class rows 9 and 14 are in: reachable through `RETURN properties(n)` and nothing else — and, like them, a property-level placement ruling rather than a free deletion |
| 6 | `:Session` core (`id`, `harness`, counters) | §2.4 | **yes** — `CONTAINS`, and `turn_count` is composed with two walks (above) | **graph** | the session→turn/claim walk is the read path and `turn_count` gates it. ⚠️ `harness` is in this row for identity only: it is a **sibling** projection in both session reads and `none`-capability (§2.4) — do not read the row as a verdict on every member |
| 7 | `:Session` properties whose ANSWER is **composed with a walk** (`turn_max_index`, `entity_links_created`; `turn_count` is in row 6 and is composed the same way) | `hosted_api.py:45028-45045` (`RETURN s.turn_max_index, s.turn_count, count(t)` → `max(count(t), mark + 1)`, the anti-phantom prior count); `session_schema.py:908-918` (`WHERE live_edges > s.entity_links_created`, `live_edges` from two `aboutObject` walks); `session_schema.py:855-862` (`WHERE stored_turns < s.turn_count`, `stored_turns` from the `CONTAINS` walk). ⚠️ `sdk.py:4373-4380` returns the mark but consumes it WITHOUT the walk — a SIBLING site, not a carrying one (§2.4) | **yes** — the returned figure is a function of the property AND the walk | **graph** | the shortfall count, the entity-link under-count and the hosted prior-count each compare a walk-derived number to the property; no `public.session` row answers any of them alone |
| 7b | `:Session` telemetry — the **sibling** members of row 7 plus the remainder (`capture_ok`, `capture_extractor`, `capture_lane`, `entity_links_attempted`, `harness`, `actor_user_id`, `capture_redactions`, `machine_id`, `model`, `client_captured_at*`, `value_nodes_*`, `draft_count`, `commit_count`) | **sibling columns / plain projections** — `hosted_api.py:24770-24802` (`list_sessions`: `s.machine_id, s.model` at `:24791`, `s.capture_ok, s.capture_extractor` at `:24797`, the provenance triplet at `:24801`, each beside `count(p)`), `sdk.py:58336-58360` (the session-detail point lookup — **no walk in the statement**), `session_schema.py:855-861`/`:916-917` (reported beside the retrofit walks), `sdk.py:19357`; point lookups and `SET`-that-reads: `hosted_api.py:22307-22308` (the commit-state load, which names BOTH `value_nodes_*` members), `:22980-22981` (`value_nodes_created`, inside the commit atom), `:22982-22985` (`draft_count`, `commit_count`), `:24493-24497` (**`value_nodes_held`** — its own `:Session` `MERGE`/`SET` with no `:CommitRecord` in the statement, §2.4's split), `sdk.py:4265-4268`; whole-node reads for `capture_redactions` (`tools/backfill_copy_turns.py:188-192`, `projection/__init__.py:14484`) | **no** — `none` capability | **`record_only`** (property-level; §6.1) | read, but by nothing that needs the graph — property projections, property guards and node dumps, not edges. ⚠️ **The four sibling members (`capture_ok`, `capture_extractor`, `capture_lane`, `entity_links_attempted`) were in row 7 until the `#13095` review**; they are siblings of the walks that made the round trip look, and §2.4's rule is what moved them. The counter increments stay graph-coupled, so see §6.1 |
| 8 | `:Object` / `:Subject` core | §2.5 | **yes** — `aboutObject` adjacency (`assembly.py:2632`), the `-[r]->` walk (`sdk.py:37927`) **and a fulltext index on `name`** | **graph** | an Event reaches its Object by EDGE; ⚠️ `supersededBy` is a **string matched by equality** (`audit.py:817`), not a walk — SQL-shaped, and an earlier draft wrongly cited it as the reason |
| 9 | `story_arc` (on **any** label) | **no Cypher query NAMES it** — but six whole-node reads carry it (`_promote_point`, `approve_merge`, `_query`, `_paginated_query`, `_get_point`'s `_cache_answer`, `_diary_read` — the family row above states each one's address at this head, re-derived there by enclosing function; ⛔ the six `sdk.py` addresses THIS cell carried were a third, never-updated copy of that list — `:27669` is `if valid_to is not None:`, `:28513` `if not pairs:`, `:30267` a `_stamp_authored_word` call, `:30366` a `params[...]` assignment — so they named no whole-node read at all, `#14183` review round 3); capability **`none`** | **no** | **`record_only`** ⛔ **move gated on a deliberate pin decision** (§5.1) | its one NAMED reader is `public.session_narrative`; the graph carries it only via `RETURN properties(n)`, which is `none`-capability by the same disposition row 14 gives `Point.mentions`; the mechanism CAN deny it, two policy pins currently refuse the entry |
| 10 | `:GraphEvent` | §2.6 | **no** | **`record_only`** ⛔ blocked | ordered scan + aggregates; no `NODE_TABLES` record table and no tier column (§5.2) — `public.journal_event` exists but is not a field-for-field superset (§6.3). ⛔ **The tier mechanism is NOT the unblock** (`#13094`): `:GraphEvent` is a second durable STORE, not a projection, and a `NODE_TABLES` row would put it on the rebuild path `#4664` forbids — `docs/evidence/13094-graphevent/README.md` §4, recorded as `#13094`'s remaining scope |
| 11 | bookkeeping labels (§2.7) | §2.7 | **no** | **`record_only`** ⛔ blocked | key lookups and label scans; no record table (§5.2) |
| 11c | `:OnboardingState` / `:OnboardingStep` | §2.7 | **yes** — `completed_steps()` walks `COMPLETED_STEP` | **graph** | the docstring is explicit: *"Canonical completed-step ids from the edge set (never a second store)"* — the edge IS the store |
| 11b | `:Tag` | §2.5 | **yes** — `TAGGED` is how every tag is read | **graph** | the edge is the read; the node is its endpoint |
| 12 | `_searchText` on a **non**-episodic `:Source` | FTS | **yes** | **graph** | the fulltext index's field set |
| 13 | `Source.summary` on an **episodic** Source | **four WRITERS, and the record door refuses to carry it.** Writers: the **live capture writer** `sdk.py:22288` (`s.summary=CASE WHEN $sum <> '' THEN $sum ELSE coalesce(s.summary, '') END` — a `SET` whose ELSE arm READS the node, §2's rule) which reads it back into the journal at `:22411` (`… s.summary, s.topics, s._searchText`); `projection/entities.py:5824` (`s.summary=coalesce($summary, s.summary, '')` — inside **`_upsert_document`** (`:5434` at this head), not `_upsert_source` (`:7240` at this head; the pin's `_upsert_source` is `:6850`, and `:7025` at the pin is the convergence-contract comment), whose own body contains no `summary` write at all); `session_source_prose_migration.py:277-279` (`_REPAIR_PAIR` — `AND coalesce(s.summary,'')=$old_sum … SET s.summary=$sum`); and the **live hosted commit door** `hosted_api.py:23501` (`_execute_commit_writes`, `:23313` — `MERGE (s:Source {url:$did}) … s.title=$title, s.summary=$summary, … s.is_episodic=true`: an **episodic** `:Source`, i.e. this row's population, written **unconditionally** — no `coalesce`, no `CASE` keep-arm, so unlike the capture writer it always overwrites). ⚠️ The fourth writer is the one that changes the row's entitlement argument: a lane that removes only the three named writers leaves this door live. Readers, all capability `none`: the record door **reads it as the WITNESS and never projects it** (`cache_projection.py:2542`, the `summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"` comparison inside `_episodic_source_search_text_is_the_lexicon` (`:2485`), whose answer the projection decision consumes at `:2724-2726` (the comment above it, `:2660-2702` at `main` (`:2660` is a `prose_columns` assignment at the pin, so the range is a comment only at `main`), explains the decline but decides nothing); the rule it implements is the `#10965`/`#10852` block at `cache_projection.py:2712-2723` at `main` (the `#10965` marker is its FIRST line; the `:2714` this used to name carries `#10852` but not `#10965`), not the comment lines that restate it below); `session_source_prose_migration.py:218-223` (`_SOURCE_ROWS`, issued by `find_session_sources_carrying_prose` and run on every rebuild via `reconcile_after_replay`), `:1263-1268` (`_HITS_ROWS`), `:1292-1300` (`_CONTROL_LEXICON_ROWS`, `WHERE s.summary STARTS WITH $prefix … AND s.documentKind IS NULL` — exactly this episodic population) | **no** — property projections, a prefix predicate and a `SET` keep-arm | **`record_only`** ⛔ **NOT shipped** | ⚠️ **the record door DECLINING it is the opposite of a move**: nothing removes the graph copy, so it is still the only copy and the **live capture writer still writes it** — **98 B** of `:Source` payload in §4 (its own sub-row there, read from the committed `measurement-before.json` → `repr_bytes_by_label.Source.summary`). The move has **no mechanism at all** — not a refused one: it is a *property-level* placement on a non-`:Point` label, and the row-level `cache_tier` cannot express a property-level move (§6.1). §5.3's third precondition covers WHOLE-LABEL moves, which this is not, so it neither blocks nor unblocks row 13 (see the note under §5.3). End-to-end pins: `tests/test_10853_session_source_prose.py:203` (`test_the_drop_is_narrow_the_session_metadata_still_projects`) and `:373` (`test_the_record_row_is_not_mutated_by_the_projection`) |
| 13b | `Source._searchText` on an **episodic** Source (BOTH derivations) | FTS — `CALL db.idx.fulltext.queryNodes('Source', $query)` (`search_engine.py:3157`) over the index declared for the WHOLE label (`projection/__init__.py:6990`), populated by both Source writers (`sdk.py:22246`, `#3518` — the capture path's `search_text = _source_search_text(topics=topics)`; `:19637` was a comment about provenance loss, not a writer) | **yes** — fulltext index | **`graph`** | ⚠️ The record door's per-ROW decline (`cache_projection.py:2724-2726`, the post-`#10000` lexicon rows) is a RECORD-projection decision, not a licence to drop the graph copy: whatever the graph holds, the index reads it, and dropping it "un-indexes a restored session" (`:2714-2715`). A pre-`#10000` row's value is prose derived FROM `summary` (`:2108-2112`) — prose, but still index input |
| 14 | `Point.mentions` (the held-phrase JSON tail prop, `projection/entities.py:1292`) | `sdk.py:30573` (`_get_point`'s `_cache_answer` — *"Returns dict of all properties"*, the whole-node read; ⛔ the `sdk.py:27265` this cell carried is a `#9836` comment line and named no read, `#14183` review round 3); `projection/__init__.py:22118-22120` — a **write-only** fold (the branch is computed in Python at `:22109` from the journal payload, so the `SET` reads no node; `none`); `sdk.py:18896` is **not** a reader of the stored shape — it reads the extractor PAYLOAD dict, `pt` bound at `:18769` | **no** | **`record_only`** ⛔ gated — the two test pins refuse the declaration (§5.1) | nothing walks, filters or indexes it, and it is **not** an embedding input (`embed_compose.py:284`'s `compose_embedding_text` takes `quote`, then `content` as the base when there is no quote, plus resolved neighbour names — the held-phrase JSON tail is none of the three; ⛔ the `sdk.py:16030` this cell carried is a bullet in an unrelated docstring, `#14183` final review) — but the move is blocked |

⚠️ **Row 13 was first corrected TWICE, in opposite directions, and the second correction is the one to remember.** The tally has since grown to four — the note below carries it. Draft one said "no Cypher reader names it" — false. This revision found the OPPOSITE error: the row carried `✅ already shipped`, which reads as "the shape has moved", while in fact the only thing that shipped is a **projection DENY** (`cache_projection.py:2656-2660` is its own comment; the executable deny is `:2724-2726`, and the doors that actually refuse the value are `:2787-2789` (tail) and `:2795` (column) — a `+5` on this family. ⛔ The `cache_projection.py` deltas are NOT uniform, so do not extrapolate one: `+3` above the ~2100 insert (`:2108-2112`), `+5` below it (`:2485`, `:2542`, `:2693`, `:2705`, `:2776`, `:2787-2789`, `:2795`), and `:3841` re-derived on its own (+102). The `+3` block was applied to the `+5` family, which is the error this sentence exists to stop) — a decision NOT to put the value in the record. A deny is not a move: it leaves the byte in the graph, written by the live capture path (§3 row 13's writer column, and the 98 B the instrument measures). ⛔ **A lane reading "already shipped" would have skipped row 13 as done**; the honest state is `record_only` by capability, **unshipped** by mechanism, and the reason is §5.3's, not a missing census item.
⚠️ **Row 13 has now been corrected FOUR times, and the fourth is a re-derivation, not a verdict.** `three WRITERS` was one short: `hosted_api.py:23501` inside `_execute_commit_writes` (`:23313`) — the same pair is `:22919`/`:22731` at `main` `ae54df166d`, `:22301`/`:22113` at the pin, 23082 / 22894 as `#14059`'s squash commit `32441c280d` on `main` left them (its PR head `57cb42f642` is not on `main`) — and 23501 / 23313 at the head this note is graded at (`main` `1b0db04894`; the pair is head-bound, #14201) — writes the same episodic `:Source.summary` **unconditionally** (`s.title=$title, s.summary=$summary, … s.is_episodic=true`), so a lane removing the three named writers would leave it live; and `entities.py:5824`'s parent symbol was `_upsert_source`, which is `_upsert_document` (`:5301` at `main` `ae54df166d`; `:5282` at the pin `ccb49741b2ec`) — the line opened, the symbol did not, which is exactly the half that survives a drift (the header above). The reader column had the mirror defect: it cited `cache_projection.py:2656-2660`, five comment lines, for a READ that lives at `:2484-2521` (pin `2479-2485` plus this family's `+5`; `:2482-2488` is `+3` and lands on the previous check). Verdict, mechanism and the 98 B are unchanged.

⚠️ **Rows 2b–2f and 5b were added by the `#13095` follow-up, and their absence was a real hole rather than a
formatting one.** The table classified every *label* and left six *property families* in no row at all — the exact
failure row 11c's `:OnboardingState` and row 11b's `:Tag` record: a lane reading the table cannot tell "no verdict
was needed" from "no verdict was written". `is_episodic` is the one that matters most in the wrong direction: it is
a predicate inside eight `CONTAINS` walks plus three `none`-capability label scans (row 2b), so tiering it would not blank a read — it would silently change what every
turn walk COUNTS. The families carry their deciding reader in the row, and §2.7's read/RMW/predicate-DELETE
vocabulary is what they are classified with.

⚠️ **Row 14 was corrected in review — twice, and the second correction is the instructive part.** It first
read `prompt-side Point.aboutEntities, mentions` with a **`graph`** verdict whose reader was "`search_engine`
flattening" — a reader with no `file:line` that does not exist. Two defects there: (a) `aboutEntities` is not
a node property — `projection/entities.py:2506` lists **`about_entities`, `aboutEntities`** under *"structural
/ edge-carried — never node props via passthrough"* (the snake entry at `:1808` sits in `_META_KEYS` as
*"handled as graph edges"*), and the edge it becomes is `aboutSubject`, already censused at §2.5, so the row
double-counted a censused shape; (b) `mentions` **is** stored (`projection/entities.py:1292`,
`_POINT_JSON_RECORD_PROPS`), but nothing needs a graph capability to read it, and the embedding text is
`quote` + edge-derived names (`embed_compose.py:284`, called at `sdk.py:18246`) — neither half is an input. ⚠️ Stated precisely: the ONE call site passes `props.get("quote"), content=content` and **no** `names=`, so the composed text at a write is the quote alone; the `names` arm exists in `embed_compose` and attaches at the recompose seam (`#11739`), which reads `aboutSubject` edges — already this row's §2.5 census, not a `mentions` reader.
⚠️ The FIRST correction then cited two things that are **not readers**: `sdk.py:18918` reads the extractor
payload dict rather than a node, and `projection/__init__.py:22118-22120` is a write-only fold that its own
definition of `RMW` (§2.7) does not cover. Both are replaced above by the real whole-node read, `sdk.py:30140`.
The verdict was right the first time and the evidence was not — which is why every cite in this document is
meant to be opened, not trusted. The move stays blocked by exactly what gates `story_arc`: the mechanism CAN
deny a tail-only key (§5.1 measures it), and the two test pins are what refuse the declaration.

⛔ **Row 13's end-to-end pin — the thing that makes "the writer still writes it" checkable rather than
argued.** `tests/test_10853_session_source_prose.py::test_the_drop_is_narrow_the_session_metadata_still_projects`
(`:203`) pins the narrowness: the episodic-Source DROP removes the prose columns and **still** projects the
session metadata, so a lane may not restore one by loosening the other.
`::test_the_record_row_is_not_mutated_by_the_projection` (`:373`) pins the other direction: projecting a row
leaves the record row itself untouched. Together they are why "the door declines it" and "the writer writes
it" can both be TRUE at once — the door's decline is a RECORD-PROJECTION decision (§2.4's rule does not reach
it), not a graph-side removal nobody measured. They are cited as `::nodeid`, not only as line numbers, because
that is the form a lane re-runs.

---

## 4. Measured bytes per shape

Corpus: `--sessions 6 --turns 10`, `--turn-chars 1500`, 384-dim vectors, record **unarmed** (so turns are
nodes — the population a `record_only` move removes from). Per-property figures are **rendered** sizes
(§1.1), with an array's stored footprint derived from its width. Full output: `docs/evidence/13083-capability-census/measurement-*.json`; every figure below is
re-derived from those files.

**⚠️ The per-property column is a RENDERED size, not the engine's stored size.** The probe reads each value
and sums `len(str(value).encode())`; `size()` cannot substitute (it refuses a scalar and returns an element
COUNT for a list). For **string** properties the two coincide and the figure is exact. For **numeric arrays
they do not**, and the gap is an order of magnitude: the rendered text of a 384-dim float vector is ~7,187
characters of digits, commas and spaces, while its stored `float32` payload is 384 × 4 = **1,536 bytes**. A
review caught the first cut publishing the rendered figure as bytes. Both are shown below; the **stored**
figure is derived from the width, never read off the rendered column.

| shape | nodes | rendered bytes | stored footprint | share (of the RENDERED total) |
|---|---|---|---|---|
| `:Point` | 66 | **668,014** | | |
| … of which `embedding` | | 474,348 (rendered) | **101,376** (66 × 384 × 4 B, `float32`) | **69.4 %** |
| … of which `content` | | 93,287 (exact — a string) | 93,287 | **13.7 %** |
| … of which `story_arc` (this arm only) | | 90,000 (exact) | 90,000 | 13.2 % |
| `:GraphEvent` | 67 | **12,768** | 12,768 (text-dominated — see the caption) | **1.87 %** |
| `:Source` | 2 | 916 | 916 (text-dominated) | 0.13 % |
| … of which `summary` (row 13's shape) | | 98 (exact — a string) | 98 | 0.01 % |
| `:Event` | 3 | 680 | 680 (text-dominated) | 0.10 % |
| `:Session` | 7 | 303 | 303 (text-dominated) | 0.04 % |
| `:Object` | 2 | 198 | 198 (text-dominated) | 0.03 % |
| `:Subject` | 2 | 139 | 139 (text-dominated) | 0.02 % |
| `:GraphEventMeta` / `:EpMeta` | 2 | 4 | 4 (INTEGER scalars — stored is ~24 B, see the caption) | 0.001 % |
| **total (rendered)** | | **683,022** | | |

⚠️ **The `stored footprint` cells for the non-`:Point` rows BELOW are the RENDERED figure, and they are a few percent
low — read them as rendered.** The two columns coincide only for a **string**: an integer scalar stores 8 bytes
while rendering shorter, so e.g. `:GraphEvent.seq` is 67 × 8 = **536 B stored** against **125 B rendered**
(`measurement-before.json`), and the label totals inherit that gap. Six of those labels are text-dominated, so
the error is within a few percent and **no conclusion in §4 or §5 depends on it** — but a cell that is neither a
rendered nor a stored figure is exactly what this table exists to avoid, so it is labelled rather than implied.
The `:Point` rows, where the vector and the prose live, are the ones that are exact (a string) or width-derived
(the vector).

(Percentages are of the rendered total, 683,022 — the only total the instrument can produce without a
per-type conversion for every property. They are used for SHAPE, not for a billing estimate.)

**Read this table carefully.** The census says the graph is **`:Point`-shaped (97.8 % of rendered bytes)** and
that inside `:Point` the two largest families are **`embedding` (474,348 B rendered, 69.4 %; 101,376 B stored)
and `content` (93,287 B rendered and stored, 13.7 %)**. ⚠️ **Those two are comparable only on the STORED
basis** (101,376 vs 93,287 B) — on the rendered basis the vector is not comparable at all, which is exactly
the ~4.7× over-report the paragraph above warns about. Quoting 14.8 % for a stored figure against a rendered
total was wrong and was corrected in review. Both families are `graph` verdicts (§3 rows 1–3).
`:GraphEvent` is **1.87 %** of payload but is the **largest non-Point resident**, and among §1's **rows 1–7** — the memory substrate (rows 1–6) plus the change feed that
is row 7 — it is the only one with **no** capability reader (the twelve bookkeeping labels of §2.7
have none either, so it is emphatically not the only label in the document); its ~190 B/node payload understates
it, because §9.1's own measurement puts it at
**801 B/turn** once block overhead is counted.

**The audit's honest conclusion is therefore not "most of the graph can leave":** it is *"the shapes that
can leave are small in payload, `:GraphEvent` is the one that repays a move, and the rest of the graph is
vector and text that a graph operation genuinely reads."*

## 5. The moves

**The census found the verdicts; three PRECONDITIONS decide which of them can move today, and the census
names all three precisely.** One move was built and then withdrawn — not because the mechanism cannot express it, but
because two policy pins in `tests/test_cache_projection.py` refuse the declaration until someone deliberately
widens them, and that decision belongs to the issue that owns the route.

### 5.1 ⛔ `story_arc` on a `:Point` — verdict `record_only`; the mechanism CAN deny it, two pins gate it

**The shape.** §9.1/D1's S1 narrative. `cache_projection.CACHE_FORBIDDEN_COLUMNS` declares it for `source`
and `event` (`#10394`); the **`:Point`** route is named as open in §9.1's own residual text ("the `point`
table is a SEPARATE route … a legacy turn snapshot whose `props` tail carried the prose can still land it on a
`:Point` (`#10429`'s label)").

**The census entitlement.** `story_arc` is **named by no Cypher query on any label**: `rg -n 'story_arc' tortoise/
-g '*.py'` returns **147** hits at `6c1a01df87` (this revision's base — the count is a property of `tortoise/`, and
this revision touches `docs/` only) and **not one** is inside a `MATCH`/`RETURN`/`WHERE`. ⚠️ **The `#13095` review
measured 149 on the same tree, and both numbers are right — the difference is the command**: without `-g '*.py'`
the same grep also scans `tortoise/_placement_table.json`, whose two extra hits are `["event", "story_arc"]` (`:23`)
and `["source", "story_arc"]` (`:75`) — the `CACHE_FORBIDDEN_COLUMNS` deny declarations, i.e. the mechanism quoted
back at itself, and not Cypher. Neither number is a reader count and neither may be quoted as one. Its one **named**
reader is `public.session_narrative` (`record_text.serve_from_record`). The live write door already refuses it
(`sdk._refuse_unpaired_narrative`).

⚠️ **`story_arc` IS read — by whole-node reads, and an earlier draft's "zero Cypher readers" was false under this
document's own rule.** Six product sites return the whole `:Point` node — `MATCH (n:Point {id:$id}) RETURN
properties(n)` at `sdk.py:27821` (`_promote_point`), `:28720` (`approve_merge`), `:30378` (`_query`), `:30474`
(`_paginated_query`), `:30573` (`_get_point`'s `_cache_answer`) and `:44202`/`:44209` (`_diary_read`'s two branches)
— so a legacy point whose `props` tail carries the prose is returned WITH that key today, and the deny's
visible effect is **`get_point` ceasing to return it for legacy points**. This is the exact error class §2.4 and
row 14 record twice for `capture_redactions` and `Point.mentions`: "unread" is false, "capability `none`" is the
real reason the verdict is `record_only`. **The verdict does not change** — a whole-node read needs none of the
five capabilities — but the entitlement sentence did, and the `before` arm's `Point.story_arc` = 90,000 B is the
proof the key is a node property at all.

**The mechanism reaches it — measured, not assumed.** `_props_from_row`'s tail filter is
`tail_denied = forbidden | {placement spellings}`, i.e. it denies the **plain** forbidden set as well as its
column-spelling widening (`cache_projection.py:2776`). So `CACHE_FORBIDDEN_COLUMNS["point"] = {"story_arc"}`
**does** drop the key from a point row's `props` tail. ⚠️ An earlier draft of this document claimed the
opposite ("no declaration in the tree can name a tail-only key") — that was **wrong**, and it was wrong in the
direction that matters: it told `#10429` the closure was impossible when it is one declaration plus a test
edit. Verified in-process: without the entry `node_from_row("Point", row)` keeps `story_arc`; with it, the key
is dropped.

**What actually refuses it is a TEST POLICY, and both pins are deliberate:**

- `tests/test_cache_projection.py:186-195` pins the dict **exactly** (`{source, event}`) so that adding an
  entry cannot slip in as "a one-line dict edit" — its own comment says the decision *"belongs in this test
  and in §9.1's placement rows"*.
- `tests/test_cache_projection.py:243-246` asserts every declared not-projectable name is a **real, exercised
  COLUMN** of that table. `public.point` has no `story_arc` column, so a tail-only entry reddens it — the pin
  cannot distinguish "a stale entry guarding a name that projects freely" from "a tail-only key, which has no
  column to exercise".

**So the decision is real and it is `#10429`'s:** either the completeness pin learns a **tail-only category**
(a declared name with no column, exercised through the `props` tail), or this route stays open. This document
records the finding and the number; it does not widen another issue's pin.

⛔ **And the fix must be a projection-only deny, not a fold deny — but for a narrower reason than an earlier
draft gave, and that draft's mechanism was wrong.** ⚠️ **An earlier draft said a `_POINT_DENY` entry would make a
`rebuild_all` DELETE the property from every legacy `:Point`. That is false.** `_POINT_DENY`
(`projection/entities.py:2555`) has one operational consumer on the WRITE path: it is passed as a skip set into
`_persist_extra_props(…, self._POINT_HANDLED | self._POINT_DENY, …)` at `:3295-3297`, which **drops the key from
the `SET n += $extra` map** — nothing is REMOVEd. It has four other executable readers, and naming them is the
point: two warning filters in the same file (`:3339`, `for key in self._POINT_DENY - self._POINT_HANDLED`; `:3389`,
`or key in self._POINT_DENY or key in self._META_KEYS`) and two in `tortoise/consistency.py` (`:136` re-exports it,
`:260` builds `_DENY_REASON` entries from it). ⚠️ **An earlier draft of this paragraph wrote "`rg -n '_POINT_DENY'
tortoise/` returns the definition, that call site, and comments" — that is false: it returns 20 hits across four
files, including the four sites just named.** Verified by opening all four: **none of them derives a
`REMOVE`/`DELETE`**, so the conclusion stands and the evidence sentence around it did not.

**The real reason the fold deny is the wrong instrument is a different one, and the code names it.** The
destruction the tree actually documents is a **whole-graph restore wipe** — `MATCH (n) DETACH DELETE n`, which
*"cannot be row-scoped … so a graph holding ANY row whose PROSE the record does not carry must NOT be restored at
all: copy that prose out first, because the wipe destroys the only copy"* (`projection/entities.py:2415-2416`,
§17.2, `#11325`). That hazard is **independent of which deny carries the entry**, which is why a fold deny does not
mitigate it. What does separate the two denies is **which payload the key arrives in**: the journal fold handles a
payload key (`_POINT_DENY`, whose own comment at `:1826-1827` describes it as the list *"for payload keys the
replay DELIBERATELY drops"*), while this route's key
arrives in a **record row's `props` tail** and is denied by `_props_from_row`'s `tail_denied` (`cache_projection.py:2776`).

⚠️ **And a graph-side clear DOES ship — scoped, and not to this population.** `update_point(<id>, story_arc=None)`
is an in-place clear (`projection/entities.py:2419-2420` is its own comment, `#11325`) that applies **only where
`public.session_narrative.narrative` still holds the text**, and the comment warns it must **never** be aimed at the
row's own `props` tail: the clear's own record write consumes that tail, so the patch merges right-hand and
**overwrites the prose**. For the legacy tail-only rows this census is about, that clear is therefore not
applicable — which is the accurate form of "deleting nothing", and it is a stronger argument for the projection
deny than the wrong one: the projection deny stops the WRITE on exactly the population where no graph-side clear
can safely reach.

**What it would be worth, measured.** Same corpus both arms (`--legacy-prose` on BOTH), and the ONLY thing
varied is the declaration: `before` pops the entry, `after` sets it. No prose-less arm is compared against a
prose-bearing one.

| arm | `:Point` payload | `story_arc` bytes | `:Point` nodes |
|---|---|---|---|
| **before** (entry absent) | 668,014 | **90,000** | 66 |
| **after** (entry present) | 578,014 | **0** | 66 |
| **Δ** | **−90,000** | **−90,000** | 0 |

That is **exactly 1,500 B/turn** over 60 seeded legacy turn points, and the whole delta is `story_arc` — `Point.content` and `Point.embedding` are identical between the arms (93,287 and 474,348 in both), which is
the check that the arms are one variable. Total `:Point` payload falls **13.5 %**.

⚠️ **Read `1,500 B/turn` as the SEEDED prose width, never as a distribution.** The instrument runs with
`--turn-chars 1500`, so the figure is the corpus's own constant coming back out (90,000 / 60 seeded turns) and it
says nothing about how large a real `story_arc` is on a tenant, nor what a per-turn saving would be elsewhere. What
it establishes is the ARITHMETIC — the delta is exactly the prose the arm seeded and nothing else moved — not an
average anyone may multiply by a real session count.

⚠️ **`used_memory` is NOT quoted for this pair.** The instrument's baseline is not stable between runs: the
committed pair differs by **847,064 B** — an order of magnitude larger than the 90,000 B payload delta, which is why
`used_memory` cannot carry a residency figure here. ⚠️ **An earlier draft also cited "49,504 B" from a prior pair;
that run is not committed anywhere in this branch, so the number is not re-derivable and has been dropped** — in a
document whose method promises every figure can be re-derived, an uncommitted figure is a claim to trust, not a
measurement.
Because the drift is run-dependent and has been observed both larger and far smaller than the 90,000 B payload
delta, any residency marginal read off it is a number the next run can contradict. The **payload** figure is
exact and deterministic; the residency figure is **unmeasured**, and saying so is the difference between a
measurement and a decoration.

**The instrument and both JSON arms are committed** (`docs/evidence/13083-capability-census/`) so `#10429` can
re-run the closure the moment the pin decision lands.

### 5.2 ⛔ NOT SHIPPABLE under this mechanism, and exactly why

The census's largest non-Point verdict (`:GraphEvent`, §2.6) and twelve bookkeeping labels cannot be moved by
`cache_tier`, and the reason is a **precondition of the mechanism**, not an oversight:

> `cache_tier` is a column on **`public.point`**. `read_record_view` applies the tier predicate **only**
> where `NODE_TABLES[label] == 'point'` (`cache_projection.py:3841`), and a `record_only` row only has
> meaning because a **record table holds it**. A shape with **no `NODE_TABLES` record table** has nowhere
> for the row to live once the graph refuses it. ⚠️ That is "no **tiered** record table", not "no record
> home exists": `public.journal_event` DOES exist for `:GraphEvent` (§6.3) — it is simply not a
> `NODE_TABLES` row and carries no `cache_tier` column, which is what the mechanism needs.

So for the twelve bookkeeping labels the sequence must be: **give it a `NODE_TABLES`
record home with a tier column** → **then** tier it. That is
`#13082` Phase 1 territory ("a migrated query must be *deleted* from the graph path in the same PR, and
the graph-side data it needed becomes a candidate for `record_only` in the next capability audit").

⛔ **`#13094` MEASURED THAT THIS SEQUENCE DOES NOT APPLY TO `:GraphEvent`, and the cell above must not be
read as prescribing it for that shape.** Two findings, both executed
(`docs/evidence/13094-graphevent/README.md` §3–§4):

- **`journal_event` is not a field-for-field superset and cannot become one by adding a column.** A
  payload-only emit writes a `:GraphEvent` row and **no journal record at all** — `_emit_event`'s own gate,
  whose shipped callers include the turn hard-delete lane — and the two stores' `seq`s are deliberately
  different coordinates (`:GraphEventMeta.last_seq` vs the replay journal's ordinal, `#12894`). §6.3's
  "prove it, then add what is missing" therefore resolves to **a dedicated record table**, not to
  `journal_event`.
- **A `NODE_TABLES` row would put the shape on the REBUILD path.** `read_record_view` projects every
  `NODE_TABLES` label with no per-label special case, so a record-sourced rebuild would try to
  re-create `:GraphEvent` from the record view — the resurrection `#4664` forbids (a rebuild leaves the
  store empty and carries only the watermark). The route is blocked today by the placement table
  (`UnknownRecordColumn`) and the record's key contract (`node_identity`'s CHECK has no `GraphEvent`);
  neither is a thing to "fix".

`:GraphEvent` is a **STORE**, not a projection: the mechanism it needs is a store-level home selection
(writes mirrored to the record first, reads re-pointed only after a coverage census, then a
preview-by-default cutover/strip verb), not a row-level `cache_tier`. Recorded as **`#13094`**'s remaining
scope — its comment 7065026251 — with six ordered steps and their falsifiers.

**Filed rather than absorbed:** the `:Point` prose gap is recorded on **`#10429`** (this route's own issue),
and the `:GraphEvent` record home is **`#13094`**. ⛔ Nothing here arms a cap, a retention rule or an
eviction policy — that is `#13084` and P0-c.

### 5.3 The three preconditions, side by side

| precondition | shapes it blocks | what unblocks it |
|---|---|---|
| **no `NODE_TABLES` record table** (and no tier column) → no tier can refuse the row | `:ContextSubscription`, the eleven other bookkeeping labels | a tiered record home, then `cache_tier` (§5.2). ⛔ `:GraphEvent` is **NOT** in this row any more: `#13094` measured that the tier mechanism must not be applied to it (§5.2's correction) — it is a STORE, and its route is a store-level home selection |
| **a policy pin** → the declaration is refused although the seam would honour it | `point.story_arc` **and** `Point.mentions` (§3 row 14) — ONE precondition, TWO shapes | `#10429`: widen the completeness pin with a tail-only category (§5.1). ⚠️ What refuses any new entry regardless of label is the *exact-dict* pin, not the completeness pin (`tests/test_cache_projection.py:186-195` compares `{source, event}` to the WHOLE `CACHE_FORBIDDEN_COLUMNS`) — which is why it blocks both shapes and why naming only `story_arc` here was an error |
| **a record table but no tier COLUMN** → having a row is not having a tier | every non-`:Point` label whose move is **whole-label** (none in this census — §3 rows 1/4/5/6/8 all read a capability and are all `graph`; §1's record-table column is NOT a move criterion, and reading it as one is the error this cell corrects) | a `cache_tier` column (one migration per label) **plus** the `if table == "point"` arm at `cache_projection.py:3841` widened; ⚠️ this does **not** unblock row 7b, which is a **property-level** move the row-level tier cannot express (§6.1) |

None of these is a census failure. Each is the mechanism's **precondition**, named so the next slice is executable
instead of another audit.

⚠️ **And a fourth class has no mechanism at all, which is a different fact from a precondition.** A
**property-level** placement — row 7b's family and row 13 — cannot be expressed by a row-level `cache_tier` on
ANY label, so neither "give it a record table" nor "add a tier column" unblocks it. It is listed here rather
than as a fourth table row because a precondition names what to build next, and this class names what does not
yet exist: a property-level tier. It also holds the most shapes in the census, which is worth stating plainly
rather than letting three named preconditions imply the mechanism is nearly complete.

## 6. Arguable shapes — listed with the question that would settle each

No guessed verdicts. Each of these is a shape the census **cannot** settle from the code alone.

### 6.1 `:Session` capture telemetry — TWO properties are graph-CARRIED; the siblings and the remainder are `record_only`

`capture_redactions`, `capture_ok`, `capture_extractor`, `capture_lane`, `harness`, `actor_user_id`,
`machine_id`, `model`, `client_captured_at*`, `turn_max_index`, `entity_links_attempted/created`,
`value_nodes_created/held`, `draft_count`, `commit_count`.

**The family splits three ways, and §2.4's participation rule decides each part.** This table replaces an earlier
"SIX properties ARE graph-read" framing, which was true of the QUERIES and false of the PROPERTIES:

| part | members | what decides it |
|---|---|---|
| **composed with a walk ⇒ `graph`** (verdict row 7) | `turn_max_index`, `entity_links_created` (and `turn_count`, row 6) | the returned figure is a function of the walk AND the property — `max(count(t), mark + 1)`, `live_edges > s.entity_links_created`, `stored_turns < s.turn_count` |
| **sibling columns ⇒ `none`, property-level `record_only`** (row 7b) | `capture_ok`, `capture_extractor`, `capture_lane`, `entity_links_attempted` | *reported beside* a walk (`session_schema.py:852-853`), and returned by a point lookup with **no walk in the statement at all** (`sdk.py:58338-58360`) |
| **the remainder ⇒ `none`, property-level `record_only`** (row 7b) | `capture_redactions`, `machine_id`, `model`, `client_captured_at*`, `value_nodes_created`, `value_nodes_held`, `draft_count`, `commit_count`, `harness`, `actor_user_id` | point lookups, `SET`-that-reads, whole-node dumps |

⚠️ **`capture_lane` was in NO row until `#13095`'s predecessor found it, and this revision moves it a second time —
with the reader opened.** It is the third member of the capture-provenance triplet (`session_projection.py:68`),
returned by the short-turns query (`session_schema.py:860-861`) and by `list_sessions` (`hosted_api.py:24801`).
⛔ **The earlier revision's reason for `graph` does not survive being opened:** it said "returned from a query whose
answer needs a `CONTAINS` walk", but in that RETURN `capture_lane` is the seventh of seven expressions and **nothing
in the statement consumes it** — `s.turn_count` is what the walk is compared to (`WHERE stored_turns < s.turn_count`).
A property the walk does not consume is a sibling (§2.4), so its own capability is `none` and it belongs in row 7b
beside `machine_id`/`model`. ⛔ **What must not happen to it is the thing the earlier revision feared**: it may not be
*dropped* — it is served by `SESSION_READ_FIELDS`/`SESSION_LIST_FIELDS` (`session_projection.py:68`, `:73-79`) and is
already a record column, so its disposition is a property-level placement ruling like `machine_id`'s, neither a
deletion nor a graph capability.

⛔ **Correction — an earlier draft of this section said *no Cypher reader names any of them*, which is
false.** `session_schema.py` names four, inside `audit_session_retrofit` (`tools/session_retrofit_audit.py`):
`capture_ok`/`capture_extractor` are returned by the short-turns query at `session_schema.py:855-862`, whose row count comes
from `OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point)`; and `entity_links_created`/`entity_links_attempted` come
back from the entity-link query at `session_schema.py:907-917`, whose row set is `WHERE live_edges > s.entity_links_created`. `session_schema.py:892`
(`WHERE s.capture_ok = true RETURN count(s)`) and `sdk.py:19357` (the `session_existed` retry decision) are
`none` on their own. ⚠️ **The conclusion that paragraph drew — "two of these readers need adjacency, so the four are
`graph`" — was the sibling error in miniature**, one draft before it was found in the two superseded sentences
below: adjacency in the statement is not adjacency the PROPERTY needs. What survives is the correct part of the
finding — all four ARE read, and a remedy that assumed otherwise *(`"if nothing reads them but a test, they are dead
weight … drop, not tier"`)* would have deleted data two shipped audit classes depend on.

What remains a placement question is the **remainder plus the four siblings** — `capture_ok`, `capture_extractor`,
`capture_lane`, `entity_links_attempted`, `capture_redactions`, `machine_id`, `model`, `client_captured_at*`,
`value_nodes_*`, `draft_count`, `commit_count`, `harness`, `actor_user_id`:

⚠️ `turn_max_index` is **NOT** in this remainder: it is `graph` (row 7). An earlier draft listed it here as
`none` on the strength of the session-detail read (`sdk.py:52345`) alone, missing the reader whose ANSWER is
the mark/count comparison over the `CONTAINS` walk (`hosted_api.py:45028-45040`) — row 7's rule applied to a
property, the same sibling confusion `capture_lane` records above, one property later. (That justification is ONE
site: `sdk.py:4399-4406` returns the mark but consumes it without the walk, and is cited in row 7 only for that
contrast.)

- **Why it is arguable:** they are **not unread** and must not be treated as such. `value_nodes_created` and
  `value_nodes_held` are both named by the commit-state load (`hosted_api.py:22307-22308`), but only
  `value_nodes_created` is incremented inside the commit atom (`hosted_api.py:22980-22981`) — `value_nodes_held` by
  its own `:Session` `MERGE`/`SET` at `hosted_api.py:24493-24497`; `machine_id`/`model`/`client_captured_at*` by the session-detail read
  (`sdk.py:58338-58360`) and again by `list_sessions` (`hosted_api.py:24770-24802`), with `client_captured_at` also
  read inside a `SET` (`sdk.py:4265-4268`); `capture_ok`/`capture_extractor`/`capture_lane` by both of those
  reads and by the retrofit audit's `RETURN`; and
  `draft_count`/`commit_count` are read inside that same commit-statement `SET`
  (`hosted_api.py:22982-22985`). The four siblings are named across three reads — `entity_links_attempted` by the retrofit audit alone:
  the session LIST (`hosted_api.py:24790`, `:24791`, `:24797`, `:24801`), the retrofit audit
  (`session_schema.py:860-861`, `:916-917`) and the session-DETAIL point lookup (`sdk.py:58338-58360`);
  `harness`'s NAMED readers are the filter-and-counts at `hosted_api.py:26535`/`:26540` **plus four id-keyed
  point lookups** (`:10693`, `:13322`, `:25151`, `:26654`) — plus the three sibling projections named just
  above (`hosted_api.py:24790`, `sdk.py:58338`, `session_schema.py:861`).
  Every one is a point lookup, a property guard or a sibling column — capability **`none`** — which is why
  the verdict is `record_only` on the CAPABILITY rule, never on the false premise "unread". Moving them is a
  **property-level** placement, which the row-level tier cannot express.
- **The question that settles it:** *does every one of those readers keep working off a `public.session`
  row?* ⚠️ For most of them the honest answer is "yes, once the row is read" — but the counters are a
  different case: `value_nodes_created`, `draft_count` and `commit_count` are incremented in the **same
  statement** as the Session `MERGE` (`hosted_api.py:22973-22989`), so their WRITE is graph-coupled even
  though their read needs no capability. That makes this a **contract/placement ruling**, not a storage
  move: `value_nodes_*` is the budget numerator the commit reconciliation consumes, and the
  capture-provenance columns are served to SDK callers and mirrored by the hosted endpoint. ⛔ An earlier
  draft's remedy here — *"if nothing reads them but a test, they are dead weight on both stores and should
  be dropped, not tiered"* — rested on a premise this section disproves, and would have deleted counters
  the commit path and the session read depend on. They are not deletion candidates; they are candidates
  for a product ruling on placement. (`capture_redactions` is the one property here with no named-column
  Cypher read — but two whole-node `properties(s)` reads carry it, `tools/backfill_copy_turns.py:188-192`
  and `projection/__init__.py:14484`, so it is read, not merely written: its disposition is the same
  placement question, not a free deletion.)

### 6.2 `:Point` listing-only families (`pointKind` filters)

⛔ **Two rules in the same file are NOT in this family.** `domain_validators.py:153-158`
(`orphan_use_case`, a 2-hop `hasPart` walk) and `:241-243` (`orphaned_draft`, `AND NOT (n)--()`) are
classified **adjacency** in §2.1 and are **`graph`** — the first correction of §2.1 filed the whole
`:106`–`:244` range as `none` and the second still missed `:153-158`. Only the pure kind/property-filter
rules are arguable here.

- **Why it is arguable:** `MATCH (p:Point) WHERE p.pointKind IN …` is Cypher, and it is **SQL-shaped** —
  it does not need a graph. Under the capability rule the shape is `record_only`; under "a Cypher reader
  exists" it may not move without blanking that reader.
- **The question:** *is the validator's latency budget met by the record?* That is a differential-harness
  measurement (`#13082` §2), not a census fact. Verdict deferred, not guessed.

### 6.3 `:GraphEvent` — the *verdict* is settled, the *move* is not

- **The question:** *does `public.journal_event` already carry every field `poll_events` serves, with a
  stable cursor?* ⛔ **The premise that this would be an easy yes is FALSE, and the code says so in three
  places.** An earlier revision of this paragraph claimed "every `:GraphEvent` emit sits behind a journal
  append" and concluded the move was "very likely" a feed re-pointing. It is not:

  1. **`_emit_event`'s own contract** (`sdk.py:16098-16103`, the paragraph's last line included —
     `:16102` is its penultimate one): the JSONL half is written only *when* `point`,
     `id` or `turns` is provided — *"Events with NONE of the three are skipped (nothing meaningful to log)."*
  2. **`append_event`'s own contract** (`event_store.py:808`, and again in its module docstring at `:58`):
     *"a payload-only emit writes one with no JSONL record at all."*
  3. **A shipped call site in exactly that class** — the turn hard delete (`sdk.py:3801`): it emits
     `PointRetracted` as an `:GraphEvent` subscriber row and *separately* calls
     `_journal_entity_mutation("Point", tid, "delete")` for the JSONL half, whose record is `EntityMutated`
     (folded by `_delete_entity_by_id`). The surrounding comment states the split as a contract — *"Two
     stores, one meaning each"* — so `journal_event` does not carry the same **event type**, let alone every
     field of it.

  A missing event type is worse than the off-by-one cursor the old paragraph warned about: a subscriber would
  see a stream that is complete-looking and silently short. **Verify the field-for-field match against
  `poll_events`, event type by event type, on a real tenant before proposing it.**

- ⚠️ **And "no record table" was the wrong words for a label whose stream table EXISTS.**
  `public.journal_event` is real (`supabase/migrations/20261012000001_record_event_stream.sql:81`), and the
  migration's own `COMMENT ON TABLE` (`:93-94`) calls it *"the append-only record of what happened
  (GraphEvent)"* — enforced by the trigger created at `:111` from the function at `:96`, with exactly one
  exception (`:104-106` permits a DELETE when `app.record_journal_purge = 'on'`, the tenant-erasure path).
  The accurate statement is **"no `NODE_TABLES` record table, and no `cache_tier` column"** — which is the
  precondition that actually blocks the move (§5.2), and a different fact from "no record home exists".
  The `#13094` work is therefore **not** "build a store" in the sense of a SECOND WRITER of the replay
  journal: it is "prove, event type by event type, that the existing stream is a field-for-field
  superset — and if it is not, add what is missing". ⛔ **`#13094` answered that, and the answer is "it is
  not, and it cannot be made one"** — a payload-only emit has no journal record at all, and the two
  `seq`s are different coordinates — so "add what is missing" resolves to a dedicated record table for
  the change feed (not `journal_event`, not `cache_tier`; `docs/evidence/13094-graphevent/README.md`
  §3–§4).

### 6.4 `:Source.embedding` on a non-episodic Source — **decided 2026-11-17, cited not reopened**

- **The decision:** a `:Source` is **to get** a summary vector, **in Postgres** (`STORAGE-ARCHITECTURE.md` §14.3 **V2**, owner
  2026-11-17, `#13220`). The demand question below no longer decides whether — it sizes what the vector is worth.
- **The DESIGN question this row used to carry is answered for the RECORD:** *which text* a `:Source` with no session
  narrative embeds — the payload's `_searchText` alone, the same key the split binds into `public.source._search_text`
  (PR `#13473`, `#13418`; `STORAGE-ARCHITECTURE.md` §14.3 V2 and `SEARCH-ARCHITECTURE.md` state it). ⚠️ The
  `title`-only door states no `_searchText` and therefore abstains by name — a TEXT-CARRIER gap whose fix is the producer
  (`#13474`), not a vector rule. The demand measurement below still informs the graph-side half; it is not a placement
  question.
- **A PLACEMENT question the row above does not cover, raised by the writer:** `_upsert_source` computes no embedding, and
  `SourceCreated` is no longer absent from `_RECORD_EMBEDDING_TEXT` (PR `#13473`, closing `#13418`; the `SourceVersioned` transition rides the same
  rule, since it is the record that moves a row's `_search_text`) — so the placement
  question this census posed is now answered for the RECORD and still open for the GRAPH. "The graph holds **0** `:Source`
  embeddings on NON-DOCUMENT rows" remains a true reading of the LIVE fold (`_upsert_source` computes none), while a
  record-sourced REBUILD of the now-occupied rows writes one, because `source` is a SERVED label
  (`cache_projection.node_statement` writes `n.embedding` for every label outside `UNSERVED_VECTOR_LABELS`). Giving the
  graph leg one is a change to where it is WRITTEN, and the ruling does not order it.
- **The measurement it still wants:** *what share of vector queries hit a `:Source` embedding?* — P0-c's query-mix
  measurement.

### 6.5 `UnservedVectorLabels` — already decided, cited not reopened

`:Session` vectors are declared unwritable (`#11747`, `UNSERVED_VECTOR_LABELS`); this census agrees and
changes nothing.

---

## 7. What this document does not do

- **It migrates no Cypher reader.** Every `graph` verdict keeps its reader untouched; that is `#13082`
  Phase 1/2 behind a differential harness.
- **It arms nothing** — no cap (`#13084`), no retention, no eviction.
- **It bounds nothing.** `journal_event` is untouched (`#9025`), and no per-graph cap or node-count rule is
  introduced (`#11758`: the price of record is a share of STORAGE, not a node count): this is *what may be
  resident*, never how many graphs or nodes a tenant may have.
- **It does not delete.** No product code changed in this slice, so nothing was removed from any graph. ⚠️ The
  destructive leg is **not** available to cite for THIS class: `#10017` ("purge the pre-`#10000` turn nodes")
  was closed **`NOT_PLANNED`** on 2026-11-06, and the tool that does ship — `tools/purge_legacy_turns.py`, the
  `DETACH DELETE` of legacy episodic turn `:Point`s — targets a different population and never names
  `story_arc` (`grep -c story_arc tools/purge_legacy_turns.py` → `0`). So there is no shipped purge to point at
  for this shape. The legacy copies this
  document declines to fold-delete therefore remain, which is a *stronger* reason for the §5.1 refusal, not a
  weaker one.
- ⛔ **It is NOT a census of the whole database.** Row 18 of §1 lists the labels found but **carrying no §2
  table row and no §3 verdict** (`:Pipeline`, the metering substrate, the tenancy/registry labels — `:Organization` among them, and it is not
  even in a tenant's graph — and the retired `:Document`). Those get **no §2-table row and no §3 verdict**
  here, and a placement decision on them must not be made from this document. ⚠️ **The reading the `d` column
  must not be given**: a record table is a PRECONDITION (§5.3), never a verdict. ⚠️ **Precisely:** `:Organization`
and `:Document` DO have their readers named at `file:line` — `:Document`'s at `migrate_kinds.py:296-300`
(a named-property read of the legacy label — `RETURN ID(n), n.id, n.url, …`, not a `properties(n)` dump; capability `none`) and `:310`
(`MATCH (n:Document:Source) RETURN ID(n)`) and `:Organization`'s at `metering.py:304` / `cohort_cost.py:301` — which
is a census in §2's sense but deliberately **no verdict**, which is the distinction this bullet exists to draw, not
an absence of work for the next lane to re-derive. That is a **gap**, not
  a scope choice dressed as one.

---

## 8. The external leg — what was taken, and what was checked here

**The question:** how do graph-database deployments decide what to keep resident when RAM is the
constraint?

**What was taken** (one Perplexity `sonar` query; ⚠️ the brief's `mcp__seo-intelligence__perplexity_search`
MCP server is **not present on this box** — no such server in the catalog and no local script — so the same
provider was queried directly, and that substitution is disclosed rather than papered over):

- Deployments size for the **working set**, not whole-graph residency; whole-graph residency is a *sizing
  goal*, sustained working-set residency is the *production acceptance criterion*.
- The acceptance metric is a **cache-hit ratio**: Neo4j publishes `dbms.page_cache.hit_ratio` /
  `dbms.page_cache.usage_ratio` with a target **above 98 %**, sizing page cache from on-disk data volume ×
  a growth factor (~1.2) and reserving heap + OS headroom. Memgraph tracks
  `memgraph_memory_res_bytes` (RSS) and its peak against available RAM.
- No FalkorDB-specific published metric was in the returned set — recorded as a gap rather than invented.

**Which claims were checked in this repo before relying on them:**

| claim taken | checked here |
|---|---|
| RAM is the scarce resource, disk is cheap | §5/§2.1: **$73/GB-month** resident vs **$0.125/GB** disk — 584× |
| resorting to disk costs a cold-restore penalty | `docs/evidence/12766-fulltext-restore/README.md` (90 000-node row): **0.032 s** index-dropped, **1.585 s** index-kept (and that one blocks the instance) |
| the vector index is a major residency term | `STORAGE-ARCHITECTURE.md` §12.1, and §4 above: `embedding` (101,376 B stored) and `content` (93,287 B) are the two largest payload families in the corpus — comparable, neither dominating |
| a shape nothing reads cannot help the hit ratio | this document's own §2: `:GraphEvent` has **zero** capability readers and is **801 B/turn** of the armed-lane residue |

**What the source did NOT license.** It is a statement about **residency sizing inside a graph**, not about
**placement between stores**; it supports "don't keep what the working set does not touch", and is *not*
evidence for any particular migration order. The migration order stays `#13082`'s (frequency × simplicity,
behind a differential harness).
