"""#7948 — the `aboutObject` read-site declaration must be COMPLETE, not just
well-formed.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13's read-site table is the
**whole evidence base** for a P1-severe storage ruling — the document says so in
its own words: *"⚠️ The count is the whole argument — the table must name every
customer-callable surface"*. The ruling it supports (KEEP the stored edge,
owner, 2026-09-23) is safe; the **table** is not.

It is a hand-maintained prose list of `file.py (symbol)` pinned to
`c79ba1cf2`, and it has been found incomplete **four separate times**:

1. the issue body (`#7948`) — the envelope, whose site count is stated in that
   issue's owner-decision comment and recorded in
   `docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md`, NOT in the body and not
   here: this line restated the numeral and it was wrong (arm 24 bans the shape);
2. review finding `B2.7` (`REVIEW-CONSOLIDATED-2026-09-23.md`) — named three
   further readers and under-classed two of them;
3. the `#7956` erratum on `B2.7`, which found that finding *itself* incomplete
   in the direction that matters (the customer-callable count was four, not
   two);
4. the review of *this* file's first revision (`PR #9398`) — five further
   readers, two of them customer-callable, **all** invisible to a census that
   only read Cypher *string literals*.

THE ROOT CAUSE, AND WHY THIS IS A CENSUS RATHER THAN A LONGER LIST
------------------------------------------------------------------
Nothing executable compared the table to the code. A **symbol resolution** check
cannot see a *missing* row, so the table could only ever be audited by hand — and
hand audits are what produced the four passes above.

The first revision of this guard was itself a hand audit in disguise: it read
`ast.Constant` only, so it could not see a relationship name **built at
runtime** from a repo constant. Five live readers were built that way
(`StateRanker._fetch_object_signals` joins `ranking.ABOUT_EDGE_TYPES`; both
`TortoiseSDK._state_related_*` concatenate `_ABOUT_TYPES`; `supersede_point` and
`_preview_supersede` loop over `SUPERSEDE_STRUCTURAL_RELS`) and every arm stayed
green over them — the census and the table agreed perfectly *because* the
census could only see what a single literal names. That agreement was circular.

So the census resolves the *expression*, not just the leaf:

* `Constant` strings — the plain case;
* `+`-concatenation and **f-strings** (`JoinedStr`, even with no placeholders),
  joined into one string;
* `Name`/`Attribute` references to a **string constant or tuple of strings**,
  module-level or class-level, in this file or (when the name is unique
  repo-wide) in the module that defines it — so an `import`ed constant resolves;
* `NAME = "a|b|…"` joined via `"|".join(NAME)`, and `for rel in NAME` loop
  variables, expanded to the constant's members — including a **`frozenset`**
  (`frozenset({...})`, `ast.Set`), which is the idiomatic shape for a rel family
  here (`DERIVABLE_STRUCTURAL_RELS`, `_ABOUT_REPLAY_RELS`, `ENTITY_LINKED_RELS`)
  and the one that escaped the resolver until the fourth review.

THREE derivations, because a reader can reach the edge in three shapes: by NAME
(the named census), through a pattern that names NO type (the empty-pattern class),
and through a type slot the resolver cannot fill (the call-time class).

WHAT THIS PINS
--------------
1. **`test_every_code_site_that_traverses_the_edge_is_declared`** — COMPLETENESS.
   The census is derived from the source's AST, not from a list: every Cypher
   string that traverses the `aboutObject` relationship *by name* — written
   literally or built from a constant — is a site, and every site must appear in
   §13's table. A new reader added anywhere under `tortoise/` reds this until it
   is declared.
2. **`test_every_declared_symbol_still_performs_the_read`** — ROT. Every symbol
   the table names must be a site of one of the three derivations below. A row
   whose symbol resolves but does not read misleads every re-verifier —
   `EXTRACTOR-V4-ARCHITECTURE`'s own rule is *"the symbol name is authoritative,
   the line number is a convenience"*.
3. **`test_the_empty_pattern_class_is_declared`** — the SECOND derivation.
   An empty relationship pattern (`MATCH (n)-[r]->(m)`) names no type, so it
   reads the edge without naming it and no scan for the name can find it —
   `recall_subgraph`'s `completeness="full"`, `entityProfile`/`tortoise_traverse`'s
   per-hop query, and the backfill census's unlabelled inbound count are all
   this shape. It is derived as a **class** from the pattern's own shape: every
   such reader under `tortoise/` must be either a §13 row or a named member of
   `EMPTY_PATTERN` (with the reason it is not an `aboutObject` read for a
   referent), and every member must still derive. One example declared by name
   was the shape's defect: it let three further members — two of them
   customer-callable — go absent while every arm stayed green.
4. **`test_the_table_is_not_vacuous`** — the census, the empty-pattern class,
   the call-time class, the table and the customer-callable count are all
   non-trivially populated, so none of the arms above can pass by finding
   nothing.
5. **`test_the_census_derives_a_relationship_name_built_at_runtime`** — the
   MECHANISM, on synthetic sources (`_MECHANISM_CASES`): a literal, a
   `+`-concatenation, an f-string, a constant FRAGMENT interpolated into the type
   slot (`{_REL}Object`), `"|".join(TUPLE)`, a `for rel in TUPLE` loop and a
   parameter default must each derive; a `MERGE` writer and a docstring that only
   QUOTES the pattern must not. One pair is about the CLAUSE rather than the name:
   a lowercase `match` must derive and a lowercase `merge` must not, because the
   clause detector was case-sensitive and such a reader derived in NO class —
   neither derivable nor declarable — while every arm stayed green. The
   rel-FAMILY constructors (`ast.Set`, `frozenset({...})`, `set({...})`,
   `tuple([...])`, `list([...])`) are the same resolver one branch further on and
   are pinned by their own arm and corpus (`_FROZENSET_CASES`,
   `test_the_resolver_follows_a_frozenset_rel_family`) — this docstring credited
   them to `_MECHANISM_CASES`, which never held them. Without these arms the
   resolver could regress to literal-only and the table would silently go back to
   being un-checked in the class that produced the fourth correction.
6. **`test_the_call_time_shape_is_derived_and_declared`** — the THIRD
   derivation, and the one this file used to DECLARE instead. A relationship
   type written as an expression (a function argument, a dict lookup, a journal
   field) leaves the type slot EMPTY when the resolver gives up on it, so the
   resolved candidate carries `-[r:]->` — a shape, like `-[r]->`, and therefore
   derivable as a class: every member must be a §13 row or an entry in
   `CALL_TIME_REL` (with the reason its gap cannot carry `aboutObject` for a
   referent), and every entry must still derive. Declaring the class by hand is
   what let `TortoiseSDK.traverse` and `TortoiseSDK.ingest` — both
   customer-callable — sit outside the census while every arm stayed green, and
   what made a NEW reader of the shape invisible by construction.
7. **`test_the_prose_counts_match_the_table`** — the NUMBERS. §13 states its
   counts in prose and the other arms check only the rows, so the numerals are
   read out of the sentences that state them and compared to the table. A
   demoted `reach` cell with a stale headline is the `#7956` erratum's direction
   — the one the ruling rests on. ⚠️ **The counts are checked WITH their units**:
   the headline used to read *"TWENTY read sites, and SIX of them are
   CUSTOMER-CALLABLE"*, mixing a **symbol** count with a **row** count, so "of
   them" could not hold — the six customer-callable rows carried nine symbols.
   It now states sites, rows, customer-callable rows and the sites inside them
   separately, and each numeral is checked against its own unit.
8. **`test_the_census_prose_carries_no_line_citations`** — the LINE HINTS. §13
   states its own rule (*the symbols are authoritative and the line numbers are
   deliberately omitted*) and then, one document over, the sibling paragraph went
   on carrying fifteen `file.py:NNNN` hints — six of them pointing at unrelated
   code at the head that rewrote it. A line number is not a claim this guard can
   check (asserting it would red on every unrelated edit), so the claim is
   removed instead of trusted: the census prose carries none.
9. **`test_the_prose_names_every_row_in_the_empty_pattern_class`** — the CLASS
   SENTENCE. §13 declares the empty-pattern class *"by rows"*, and that list is
   a prose restatement of arm 3's derived set: it said *"rows 3 and 17"* while
   the class had three members (row 18 is the backfill census). An
   under-counted declaration is the same defect as an under-counted table, one
   level up, so the sentence is compared to the derived set.
10. **`test_the_reach_column_names_only_symbols_that_exist`** — the `reach`
   COLUMN'S SYMBOLS. Every backticked identifier in `reach` must exist in the
   package: row 7 cited `QueryOptions`, which is defined nowhere. The arms above
   resolved the `site` column only, so an unguarded column could name a symbol
   that never existed and stay green.
11. **`test_the_change_chain_ends_at_the_headline_counts`** — the CHRONOLOGY.
   §13's change chain (*eleven → fifteen → twenty → …*) explains why the count
   moved, and at the head that added rows 19 and 20 it stopped at **twenty-two
   sites** under a headline of **TWENTY-FOUR**: the paragraph narrating the number
   disagreed with the number. The last increment of each unit must arrive at the
   table's total, so a chain that stops short reds. It cannot be folded into arm
   7 — the history numerals match none of the four asserted sentence forms, and
   must not, because an increment is a historical claim rather than a total.
12. **`test_the_numeral_parsing_accepts_digits`** — the SPELLINGS. `_numeral`
   documented that digits are accepted while all five captures were words-only,
   so `token.isdigit()` was dead code and a digit rewrite of the headline matched
   nothing, redding arm 7 with *"no longer states its site count"* — a false
   cause. Both spellings of the same claim are now fed through every unit and
   through the chain form, so the arm reds if the captures regress.
13. **`test_the_empty_pattern_register_count_matches_the_prose`** — the SIZE of the
   `EMPTY_PATTERN` register. §13 states its size in the same sentence that
   declares the class (`(<N> members today`), and nothing read that numeral: the
   register was only floor-checked, so adding or removing a member falsified the
   sentence silently — the same rot class the arms above close for the headline
   counts.
14. **`test_the_empty_pattern_class_derivation_holds`** — the MECHANISM for arm 3,
   on the synthetic sources in `_EMPTY_PATTERN_CASES`: a bracketed traversal with
   no type must derive, and the near-misses must NOT (a named type, a `MERGE`
   writer, a docstring quoting Cypher). The lowercase spelling of the governing
   clause is in here too, for the same reason as arm 5's: `match (n)-[r]->(m)` is
   the same read as the uppercase form, and the clause detector could not see it. Without it the shape walk could regress to
   "finds nothing" and arm 3 would report a class of size zero as complete. This
   entry used to state a count of its own (`seven`) against a corpus of nine — the
   exact defect class the arms below exist for, in the one place nobody read it —
   so the count is gone rather than re-stated; arm 19 pins the corpus instead.
15. **`test_the_sibling_paragraph_names_every_customer_callable_row`** — the SIBLING
   SET. `EXTRACTOR-V4-ARCHITECTURE.md` §16 question 2 enumerates the
   customer-callable surfaces, and at the head that added rows 21/22 it was two
   rows short of §13 — the two customer doors whose addition is what moved the
   numeral the KEEP ruling rests on. Every customer-callable row's symbol must
   appear in that paragraph.
16. **`test_the_callable_enumeration_lists_each_surface_once`** — the SIBLING LIST.
   §13's `⇒` sentence is the reviewer's checklist for "did every customer-callable
   surface get named", and it named the `tortoise_supersede` dry-run preview
   twice — ten entries for nine surfaces, read by nothing. A duplicate entry reads
   as a surface checked twice while another went unchecked, so the entries are
   checked twice: no duplicate, and the distinct count against the numeral the
   sentence itself states (`are nine of them` refers to the surfaces just listed).
   Comparing them to the TABLE's row count was the units error this file exists
   for, one sentence over.
17. **`test_a_pattern_built_entirely_from_a_variable_is_unreachable_and_says_so`**
   — the stated LIMIT. A Cypher pattern assembled wholly from variables carries no
   bracket text for any of the three walks, AND is undeclarable: arm 2 requires
   every §13 row to be a site of a walk, and arm 6's reverse direction requires
   every register entry to still derive. The docstring used to promise the
   opposite ("must be declared by name — arm 6 checks that declaration"); the arm
   measures the gap and pins the honest statement, so the claim cannot drift back.
18. **`test_the_file_filter_un_sees_nothing`** — the FILTER. `_files()` is a
   text-predicate gate in front of three walks, and the PR body's claim that it is
   a strict superset was measured once by hand and pinned nowhere: a NARROWED gate
   would un-see a reader silently instead of reding. The three walks are run over
   the modules the gate SKIPPED (a gated module's sites are already the cached
   walks' output) and must find nothing in any class, and a deliberately narrowed
   filter (the literal carriers only) must be shown to DISAGREE — so the arm
   proves it can see a narrowing rather than asserting it, at ~2 s rather than the
   ~19 s a full 242-module re-walk cost.
19. **`test_the_case_corpora_are_not_vacuous`** — the CORPORA. Every synthetic
   case tuple must hold both directions (something that derives and a near-miss
   that must not), no repeated labels, and a floor on its size: a corpus silently
   shrunk to one direction leaves the arm above it green and unmeasured.
20. **`test_the_call_time_shape_derivation_holds`** — the MECHANISM for arm 6, on
   the synthetic sources in `_CALL_TIME_CASES`: the exact analogue of arm 14 for
   the third class, and it was missing from this list while arm 14 was not. An
   empty type slot must derive, and the near-misses must NOT (a named type, an
   empty PATTERN, a `MERGE` writer). Without it the call-time walk could regress to
   "finds nothing" and arm 6 would report a class of size zero as complete.
21. **`test_a_new_call_time_reader_cannot_ship_green`** — the NEW-MEMBER direction
   of that class, run on `_NEW_READER_CASES`: sources the tree does not contain are
   pushed through the completeness arm's OWN predicate, so *"a new member of the
   shape cannot ship green"* is a measurement rather than a claim about the
   derivation. Also missing from this list, and it is the arm the PR body cites as
   the sixth review's headline fix.
22. **`test_the_docstring_names_every_arm`** — the NUMBERING OF RECORD, checked
   against the arms themselves. This list called itself the numbering of record
   while two arms had no entry at all (20 and 21 above, which is how they were
   found), so the promise is now executable: every `test_` function is either a
   numbered entry here or a declared collapse (`_COLLAPSED_ARMS`), the numbers are
   contiguous with no duplicates, and every collapsed name is a real arm still
   named in the prose. A count in a comment is exactly the claim class this file
   exists to stop being unread.
23. **`test_the_extractor_section_keeps_its_three_questions`** — the SIBLING
   DOCUMENT'S OWN LIST. `EXTRACTOR-V4-ARCHITECTURE.md` §16 prefaces its list with
   *"The three questions below"* and §15 cross-references *"question 3"*, and this
   PR's rewrite of item 2 removed the newline before item 3 — the question became
   the tail of the paragraph above it (`…cheap to keep.**3. **Should every
   expansion…`), so the list rendered one item short while the sentence above it
   still claimed three. Nothing here could see it: the line-citation arm scans
   that line for `file.py:NNNN` and the sibling arm reads backticked symbols,
   and both survive a missing newline. So the count the sentence states, the
   items that start their own line and their numbering are compared — and the
   predicate is run on a glued sample and a clean one, so it is measured rather
   than trusted.
24. **`test_the_file_states_no_count_it_does_not_measure`** — this FILE'S OWN
   NUMERALS. The register's size lives in §13's sentence and in the
   `EMPTY_PATTERN` tuple, and the arm above reads it from the DOCUMENT — so three
   present-tense copies of the old number sat in this file's comments while the
   arm stayed green, and `_MIN_CALL_TIME` carried the same kind of copy for the
   call-time walk's member count, one numeral behind. Both are now stated as
   properties that point at the arm which measures them, and this arm bans the
   copies so they cannot come back: a count is asserted, or it is rot. The same
   ban covers the `#7948` envelope's site count, which lives in that issue's
   owner-decision comment and in the changelog — the docstring restated it in two
   spellings the original ban could not see, and both are matched now.

WHAT THE CENSUS CANNOT SEE (stated, not silently excluded)
---------------------------------------------------------
* **An empty relationship pattern** names no type, so nothing literal or
  constant carries `aboutObject` — no scan for the NAME can find it. It is not
  left undeclared: the class is derived by arm 3's shape walk, and every member
  must be a row or an `EMPTY_PATTERN` entry. The walk covers **both legal
  spellings**, the bracketed `-[r]->` and the bracket-less `(n)--()`: the second
  one used to be outside it, and a live member of it —
  `domain_validators.validate_chain_integrity`'s `AND NOT (n)--()`, the
  `orphaned_draft` degree check — sat undeclared while §13 claimed every such
  reader was enumerated, and the register was only floor-checked so nothing
  noticed. A pattern assembled **entirely from
  a variable** (no bracket text anywhere in the source) is out of reach of that
  walk — and it is **neither derived nor declarable**: a §13 row for it reds arm 2
  ("declared as a reader but no Cypher in it traverses `aboutObject`") and a
  `CALL_TIME_REL`/`EMPTY_PATTERN` entry for it reds arm 6's reverse direction ("the
  call-time walk no longer derives it"). Closing that would need a NEW shape walk
  for how the string is assembled, or a row/register exemption — not a new hand
  entry. **This is a stated limit of the census, not a covered case**: arm 17
  measures it and pins this sentence, because an earlier revision claimed the
  declaration route existed and a reader who trusted it believed the census was
  total. A parameter DEFAULT (the shape `link_entity` uses) is resolvable and is
  derived instead.
* **Resolution is one level deep per binding.** A rel name assembled from a
  value that is itself computed at call time (a function argument, a dict
  lookup, a value stored on an instance at construction) is not resolved; the
  placeholder it leaves behind is a *gap the literal around it still fills*, so
  a query whose rel pattern is readable still derives. Anything assembled
  entirely from such values must be declared by name (arm 6) or a row will be
  missing.
* **Write arms are out of scope.** A `MERGE`/`CREATE` that mints the edge is a
  writer, separated from a reader by the governing Cypher clause — not by a
  keyword appearing anywhere in the statement. A read on the *write path*
  (e.g. `supersede_point` moving the edge) IS a row: it reads before it writes.

NOT promised, and deliberately so:
* Line numbers are NOT asserted. The documents' own rule makes the symbol the
  authority and the line a convenience, and pinning line numbers would make
  this guard red on every unrelated edit — a different way to be dead.
* Whether a declared site is *reachable* (default-ON, flag-gated, eval-only) is
  NOT asserted: that column is prose with reasons, and a regex over the whole
  of it would pin wording rather than behaviour.

The customer-callable **count** IS asserted (arm 7 — the `WHAT THIS PINS` list
below is the numbering of record; earlier insertions moved this arm and a stale
"arm 6" survived here for one revision), because §13 calls it *"the
whole argument"*: the arm reads the numeral out of the sentence that states it
and compares it to the `reach` column, so demoting two rows without moving the
number — the `#7956` erratum's exact direction, and the error the KEEP ruling
rests on — reds instead of shipping. The same arm checks the census-size
numerals (sites, rows and the sites inside the customer-callable rows) against
their own units, so the numerals are never restated here a second time: a copy in
this docstring is the same circular agreement the table's sibling copy was. Only
the numeral is read, never the prose around it, so the sentence can be rewritten
freely as long as it stays true.

Static, stdlib-only, no network / DB / graph — it reads source text and a
document.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORTISE = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
EXTRACTOR = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The edge the §13 ruling is about.
EDGE = "aboutObject"

#: The Cypher clauses that can govern a relationship pattern. `MERGE`/`CREATE`
#: mint the edge, so they are writers; `MATCH`/`OPTIONAL MATCH` are the readers.
#: `re.IGNORECASE` is load-bearing, not decoration: Cypher keywords are
#: case-insensitive, so `match (n)-[:aboutObject]->(m)` is the same read as the
#: uppercase spelling. Without the flag such a reader derived in NO class — both
#: consumers `.upper()` a capture the regex could never lowercase, so the
#: normalization was dead code — and the completeness arm stayed green over it.
#: The trailing `\(` is load-bearing for the same reason and was measured, not
#: assumed: a clause in Cypher is always followed by a node pattern, while a prose
#: sentence is not. Case-insensitivity alone derived a FALSE site in
#: `value_extractor.GROUNDING_SYSTEM` — an LLM prompt whose prose says "match
#: summary items" and whose JSON schema then carries `[{"edge":
#: "aboutObject|…"}]`, so the English verb governed a bracket. A guard that reads
#: prose as Cypher is the same defect pointing the other way, and both
#: directions are pinned in `_MECHANISM_CASES`.
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\b\s*\(", re.IGNORECASE)
#: A traversal: an opening `[` or `-` shortly before the relationship name, so
#: prose ("an ``aboutObject`` edge") is not mistaken for Cypher.
_TRAVERSAL = re.compile(rf"[-\[][^\n]{{0,140}}?{EDGE}", re.IGNORECASE)
#: A bracketed traversal, whatever its type slot holds (`-[r]->`, `<-[r]-`,
#: `-[r:aboutObject]->`, `-[r:{rel}]->`). `_type_slot` decides what the bracket
#: names; this only finds the brackets. The bracket-free spelling of the same
#: traversal is `_BARE_TRAVERSAL` — both must be walked, or the class is half-seen.
_EMPTY_TRAVERSAL = re.compile(r"[-<]\s*\[([^\]]*)\]\s*[-<>]")
#: The OTHER legal spelling of a relationship pattern that names no type: a
#: BRACKET-LESS traversal — `(n)--()`, `(n)-->(m)`, `(n)<--(m)`, `(n)<-->(m)`.
#: It is the SAME shape as `-[r]->`: no type is written, so it walks every
#: relationship type and `aboutObject` is one of them. The bracket detector above
#: cannot see it, and that was not academic — a live member existed while this
#: regex did not (`domain_validators.validate_chain_integrity`'s
#: `AND NOT (n)--()`), so §13's *"every such reader is enumerated by its own shape
#: walk"* was false at that head. Anchored between two node patterns (`)` … `(`)
#: on purpose: a bare `--` in prose, a CLI flag or a `->` in a log message is not
#: a traversal, and a Cypher relationship always sits between two nodes.
_BARE_TRAVERSAL = re.compile(r"(?<=\))\s*(?:<-->|<--|-->|--)\s*(?=\()")
#: The text signature of a CALL-TIME type slot — a traversal whose relationship
#: type is written as an expression rather than a name: an f-string interpolation
#: inside the bracket (`-[r:{rel}]->`, `-[:{EDGE}]->`, including one that carries a
#: property map: `-[r:{rel} {confidence:$c}]->`), or a `+`-concatenated fragment
#: where the literal ENDS inside the pattern (`"MATCH (n)-[r:" + rel + "](m)"`).
#: Only a file FILTER (a file that cannot be parsed cheaply is not walked);
#: `_is_gap_type_read` decides, on the resolved candidate.
_GAP_TYPE_HINT = re.compile(
    r"[\[<][^\]\n]*:[^\]\n]*\{[^\]\n]*\]|[\[<][^\]\n]*:\s*[\"']"
)
#: The most candidate strings one expression may expand to. A rel list is at most
#: a handful of members, so this only bounds pathological input.
_MAX_CANDIDATES = 64
#: What an unresolvable interpolation contributes: a GAP, so the literal around
#: it still derives. Deliberately empty rather than a marker string — the point
#: is to keep the surrounding Cypher intact, not to guess the value.
_UNKNOWN = ""
#: A backticked token in §13's `site` column that can be a symbol.
_SYMBOL = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?\Z")

#: Floor for the empty-pattern class walk, so the class arm cannot pass by
#: deriving nothing. The exact size is asserted by the class arm, never here: a
#: floor is not a count, and a numeral restated in a comment is the claim class
#: this file exists to stop being unread
#: (`test_the_file_states_no_count_it_does_not_measure`).
_MIN_EMPTY_PATTERN = 5

#: Floor arms: deliberately BELOW today's census (20 named sites / 22 rows at the
#: time of writing — the numerals are historical, which is the point: a floor is
#: not a count, and pinning it at today's exact figure would turn a legitimate
#: contraction into a failure whose message says "the parser broke"). They are
#: below deliberately, because a row deleted together with its reader is not a
#: reason to red. The floor exists only so no arm can pass by finding nothing.
#: ⚠️ The exact counts are asserted against the TABLE, one arm down — never here.
_MIN_SITES = 10
_MIN_ROWS = 10
#: `FOUR` was the floor when this arm was written, i.e. the count BEFORE the
#: fifth correction (four → six customer-callable rows). Kept at that historical
#: value so a *contraction* of the prose is visible even before the exact-count
#: arm below reads the numeral itself; the table declares seven today.
_MIN_CUSTOMER_CALLABLE = 4

#: Floor for the call-time class walk, for the same reason as
#: `_MIN_EMPTY_PATTERN`: the shape detector breaking is a failure, a legitimate
#: contraction is not. No numeral here — the class arm measures the size.
_MIN_CALL_TIME = 4

#: The empty-pattern readers that are NOT an `aboutObject` read for a referent,
#: declared by name with the reason, so a NEW member of the class cannot appear
#: silently: `test_the_empty_pattern_class_is_declared` derives the class from
#: the pattern's shape and requires every member to be either a §13 row or an
#: entry here. Each entry is `(filename, symbol, why it is not an about read)`.
EMPTY_PATTERN: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/domain_validators.py",
        "validate_chain_integrity",
        "the `orphaned_draft` rule's zero-edge check (`AND NOT (n)--()`) — a "
        "degree count over every edge type, used to decide whether a Point is "
        "orphaned; the same read `session_source_migration._degree` is registered "
        "for one file over. It asks nothing about a referent, and its bracket-less "
        "spelling is why the class walk had to cover both spellings: it was live "
        "and invisible while only the bracketed form derived",
    ),
    (
        "tortoise/hosted_api.py",
        "_export_graph_snapshot",
        "a whole-graph export — it copies every node and edge, so it carries the "
        "edge as data, not as a read for a referent",
    ),
    (
        "tortoise/hosted_api.py",
        "_load_commit_graph_state",
        "restore/rollback bookkeeping — it rewires every edge type it finds, "
        "`aboutObject` included, but asks nothing about a referent",
    ),
    (
        "tortoise/hosted_backup.py",
        "_independent_counts",
        "an edge-count cross-check (`MATCH ()-[r]->() RETURN count(r)`) — it "
        "counts the edge, it does not read it for a referent",
    ),
    (
        "tortoise/hosted_backup.py",
        "_restore_copy_settled",
        "the post-restore settle check, again a total-edge count",
    ),
    (
        "tortoise/hosted_backup.py",
        "dump_graph",
        "the dump cursor walks every edge type to serialise them",
    ),
    (
        "tortoise/hosted_backup.py",
        "restore_graph",
        "the restore replay that writes those edges back",
    ),
    (
        "tortoise/mcp_server.py",
        "_preview_delete_edges",
        "the delete preview enumerates every edge touching the node — a "
        "deletion inventory, not an about* read",
    ),
    (
        "tortoise/mining.py",
        "verify_no_auto_wire",
        "the no-auto-wire verifier walks every edge type to assert a shape is "
        "absent",
    ),
    (
        "tortoise/projection/__init__.py",
        "_rebuild_all_replay",
        "the rebuild/rollback replay — every edge is deleted and rewritten by "
        "type, so it carries the edge rather than reading it",
    ),
    (
        "tortoise/projection/entities.py",
        "_materialize_connector_source",
        "connector-source materialisation rewires the node's edges by type",
    ),
    (
        "tortoise/sdk.py",
        "_connected_pairs",
        "a connectivity inventory (which node pairs are joined at all) — the "
        "edge TYPE is deliberately ignored",
    ),
    (
        "tortoise/sdk.py",
        "_promote_incident_operators",
        "an endpoint-liveness gate for operator promotion: it reads every "
        "outgoing edge of the incident operator to decide whether all its "
        "endpoints are live, and asks nothing about a referent",
    ),
    (
        "tortoise/sdk.py",
        "backfill_v25",
        "a migration — it counts all edges as a baseline",
    ),
    (
        "tortoise/sdk.py",
        "provenance",
        "the provenance walk reports which edge types reach a Subject; an "
        "`aboutObject` edge is not a provenance link and is not classified as "
        "one",
    ),
    (
        "tortoise/session_source_migration.py",
        "_degree",
        "a degree count over every edge type, used to decide whether a Source "
        "is orphaned",
    ),
    (
        "tortoise/session_source_migration.py",
        "_delete_if_orphan",
        "the orphan sweep deletes every edge of a degree-0 Source",
    ),
    (
        "tortoise/session_source_migration.py",
        "_rel_types",
        "an inventory of the edge TYPES on a node, reported as labels",
    ),
    (
        "tortoise/subgraph.py",
        "_hub_degrees",
        "hub-degree counting for the fan-out cap — it counts every edge of a "
        "node, whatever its type",
    ),
)

#: The readers of the THIRD shape: a relationship type that is an EXPRESSION, not
#: a name — a function argument, a dict lookup, a journal field, a value imported
#: from a module this census does not parse. The resolver follows a name one level
#: deep and then leaves a GAP, so the resolved candidate carries an EMPTY type
#: slot (`-[r:]->`) and the shape walk (`_call_time_sites`) derives the class the
#: same way `_is_empty_pattern_read` derives the no-type one.
#:
#: ⚠️ This register is NOT the class. The class is derived; this is the list of
#: its members that are NOT an `aboutObject` read for a referent — gated to the
#: operator rels, a fixed non-about* edge, or a by-type edge rewrite. Each entry
#: is `(filename, symbol, why the gap cannot carry `aboutObject` for a referent)`,
#: and `test_the_call_time_shape_is_derived_and_declared` checks BOTH directions:
#: every derived member is a §13 row or an entry here (so a NEW reader of the
#: shape cannot ship green), and every entry still derives (so a stale entry
#: cannot make the next reader's absence invisible).
CALL_TIME_REL: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/analyze.py",
        "_bfs_select_operators",
        '`safe_rel`, a `for rel in rel_types` loop variable — the loop body skips '
        'every rel that is not `"IMPL"`/`"NAND"`, so the expansion queries cannot '
        "carry `aboutObject`; they walk the operator graph, not a referent",
    ),
    (
        "tortoise/commit_ops.py",
        "apply_payload_operators",
        '`_edge_type`, `"hasPart"` or the operator `op_type` — the `#4971` '
        "idempotency probe reads the exact edge `create_operator` would have "
        "written (operator → input), and the property map in the bracket "
        "(`[:{_edge_type} {idx:0}]`) is why the type slot stays a gap",
    ),
    (
        "tortoise/projection/__init__.py",
        "_capture_onboarding_snapshot",
        "`onboarding.state.COMPLETED_STEP_EDGE` (`\"COMPLETED_STEP\"`) — a fixed, "
        "non-about* step edge; imported INSIDE the function, and the defining "
        "module carries no edge literal, so the resolver cannot follow the "
        "binding and the slot stays a gap. It also does not derive the named "
        "census, because the value it resolves to contains no `aboutObject`",
    ),
    (
        "tortoise/projection/__init__.py",
        "_rebuild_all_replay",
        "`etype`, the `edge_type` field of a journaled replay event — the "
        "rebuild/rollback replay deletes and rewrites every replayed edge BY "
        "TYPE, so it carries the edge rather than reading it for a referent "
        "(registered for its empty patterns too: an edge can be replayed "
        "without its type being named)",
    ),
    (
        "tortoise/projection/entities.py",
        "_fold_edge_message_changed",
        '`rel`, from the event, gated to `("IMPL", "NAND")` before the query — '
        "an operator-edge attribute fold, not a referent read",
    ),
    (
        "tortoise/sdk.py",
        "_find_operator",
        '`edge_rel`, `"hasPart"` or the operator `op_type` — the dedupe probe '
        "collects an operator's targets, never a referent",
    ),
    (
        "tortoise/sdk.py",
        "create_direct_edge",
        "`rel_type`, `op_type` (IMPL / NAND, `create_operator`'s edge set) — the "
        "existence pre-count and attribute SET for an operator edge",
    ),
)

#: The table header that identifies §13's declaration.
_TABLE_HEADER = "| # | site |"

#: The §13 bullet whose numerals the ruling rests on. Everything between this
#: line and the next top-level bullet is the `aboutObject` argument; the section
#: also carries numerals about other rulings (a `123`-hub cap), so the count arm
#: must not read past the bullet.
_BULLET_START = "- **`aboutObject` as a stored edge vs a derived join**"
#: One numeral, as `_numeral` can read it: a number word (`TWENTY`), a
#: hyphenated number word (`twenty-four`), or **digits** (`24`). The capture used
#: to be words-only, so `token.isdigit()` in `_numeral` was unreachable code and a
#: rewrite of the headline as `24 read sites` produced NO match at all — the arm
#: then redded with *"§13 no longer states its site count … restore the
#: sentence"*, a false failure naming the wrong cause for a rewrite the guard's
#: own contract calls acceptable. The digits arm is exercised by
#: `test_the_numeral_parsing_accepts_digits`.
_NUMERAL = r"[A-Za-z][A-Za-z-]*|\d+"

#: `<N> read sites` — the SITE count, asserted.
_PROSE_SITES = re.compile(rf"\b({_NUMERAL})\s+read sites\b", re.IGNORECASE)
#: `<N> rows` — the ROW count, asserted. The two units are different (a row may
#: name several sites), which is why each numeral is read with its own noun.
_PROSE_ROWS = re.compile(rf"\bacross\s+({_NUMERAL})\s+rows\b", re.IGNORECASE)
#: `<N> of those rows (<M> sites) are CUSTOMER-CALLABLE` — rows and the sites
#: inside them, asserted separately.
_PROSE_CALLABLE_ROWS = re.compile(
    rf"\b({_NUMERAL})\s+of those rows\s*\(\s*({_NUMERAL})\s+sites\s*\)"
    r"\s+are\s+CUSTOMER-CALLABLE",
    re.IGNORECASE,
)
#: `<N> of them are CUSTOMER-CALLABLE` — the older phrasing; still a claim.
_PROSE_CALLABLE = re.compile(
    rf"\b({_NUMERAL})\s+of them are customer-callable\b", re.IGNORECASE
)
#: `... are <N> of them` — a later sentence re-asserting the same count. Any
#: sentence that restates the number is a claim, so it is checked too: the
#: `#7956` erratum was exactly a second sentence contradicting the first.
_PROSE_CALLABLE_AGAIN = re.compile(rf"\bare\s+({_NUMERAL})\s+of them\b", re.IGNORECASE)
#: `<A> → <B> sites` / `<A> → <B> rows` — one increment of the CHANGE CHAIN in
#: §13's chronology paragraphs. The chain exists to explain why the count moved,
#: so its LAST increment must arrive at the total the headline states; a chain
#: that stops short narrates a table two sites smaller than the one above it.
_PROSE_CHAIN = re.compile(
    rf"\b({_NUMERAL})\s*(?:→|->|—>)\s*({_NUMERAL})\s+(sites|rows)\b", re.IGNORECASE
)
#: `(<N> members today` — the SIZE of the `EMPTY_PATTERN` register, which §13 states
#: in the same bullet as its other numerals. It was the one census-adjacent
#: numeral in that sentence nothing read: the register is floor-checked
#: (`assert EMPTY_PATTERN`) but a member added to or removed from it would falsify
#: the sentence's numeral without reding anything. This module quotes that
#: sentence with its numeral NOWHERE — only this reader may state it, which
#: `test_the_file_states_no_count_it_does_not_measure` enforces.
_PROSE_EMPTY_PATTERN = re.compile(rf"\(({_NUMERAL})\s+members today\b", re.IGNORECASE)

#: The number words §13 states its counts in. Digits are accepted as well, so
#: a rewrite that writes `6` instead of `SIX` stays checkable.
_NUMBER_WORDS = {
    "zero": 0,
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
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
}


class _Index:
    """The string-constant bindings one module offers.

    Module level and per class, kept separately: `self._ABOUT_TYPES` is an
    attribute lookup, `SUPERSEDE_STRUCTURAL_RELS` a module global, and a
    resolver that merged the two namespaces would answer the wrong question for
    a class attribute shadowing a module name.
    """

    def __init__(self, tree: ast.Module):
        self.consts: dict[str, ast.AST] = {}
        self.classes: dict[str, dict[str, ast.AST]] = {}
        self._collect(tree.body, self.consts)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                attrs: dict[str, ast.AST] = {}
                self._collect(node.body, attrs)
                self.classes[node.name] = attrs

    @staticmethod
    def _collect(body: list[ast.stmt], into: dict[str, ast.AST]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        into.setdefault(target.id, stmt.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and stmt.value is not None
                and isinstance(stmt.target, ast.Name)
            ):
                into.setdefault(stmt.target.id, stmt.value)


class _File:
    """One parsed module: its tree, its parent map, its constant index."""

    def __init__(self, path: Path, rel: str, source: str):
        self.path = path
        self.rel = rel
        self.source = source
        self.tree = ast.parse(source)
        self.index = _Index(self.tree)
        self.parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self._assigns: dict[int, dict[str, ast.AST]] = {}
        self._users: dict[str, list[str]] | None = None

    def own_assigns(self, fn: ast.AST) -> dict[str, ast.AST]:
        """A function's own assignments, resolved once per function."""
        key = id(fn)
        if key not in self._assigns:
            self._assigns[key] = _own_assigns(fn)
        return self._assigns[key]

    def const_users(self) -> dict[str, list[str]]:
        """`{name: [functions referencing it]}` — resolved once per module.

        The module-constant attribution below needs this for every constant
        literal; recomputing it per literal walked `sdk.py`'s 30k nodes several
        times over.
        """
        if self._users is None:
            users: dict[str, set[str]] = {}
            for node in ast.walk(self.tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Name):
                        users.setdefault(inner.id, set()).add(node.name)
            self._users = {name: sorted(found) for name, found in users.items()}
        return self._users


@lru_cache(maxsize=1)
def _sources() -> dict[Path, str]:
    """Every module under `tortoise/`, read once (text only — parsing is lazy)."""
    return {
        path: path.read_text(encoding="utf-8") for path in sorted(TORTISE.rglob("*.py"))
    }


@lru_cache(maxsize=1)
def _literal_files() -> tuple[_File, ...]:
    """The modules whose TEXT carries the edge name — the constant source.

    Any constant whose value contains the edge name has a literal for it in the
    module that DEFINES it (`SUPERSEDE_STRUCTURAL_RELS` in `sdk`, `ABOUT_EDGE_TYPES`
    in `ranking`), so this set is complete for the constant index below and 8x
    smaller than the package — parsing all 242 modules cost ~4 s of the suite's
    wall clock for nothing.
    """
    return tuple(
        _File(path, str(path.relative_to(ROOT)), source)
        for path, source in _sources().items()
        if EDGE in source
    )


@lru_cache(maxsize=1)
def _files() -> tuple[_File, ...]:
    """The modules the census walks: literal carriers, plus constant consumers.

    A module can build its rel pattern out of `SUPERSEDE_STRUCTURAL_RELS` without
    ever writing the literal, so the second half of this set is what keeps the
    census from reproducing its old blindness one level up. The third half is
    the empty-pattern class: a module that walks EVERY relationship type names
    neither the edge nor a constant, so it is found by the shape of its pattern
    (`_may_traverse_every_type`) — which is how `navigation.py` and the backfill
    census were missing from a census that had already declared the class. The
    fourth half is the call-time class, whose module may name neither the edge
    nor any type at all (`_may_have_gap_type`).
    """
    parsed = {file.path: file for file in _literal_files()}
    names = _rel_constant_names()
    for path, source in _sources().items():
        if path in parsed:
            continue
        if (
            any(name in source for name in names)
            or _may_traverse_every_type(source)
            or _may_have_gap_type(source)
        ):
            parsed[path] = _File(path, str(path.relative_to(ROOT)), source)
    return tuple(sorted(parsed.values(), key=lambda file: file.path))


@lru_cache(maxsize=1)
def _module_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Module-level constants defined **exactly once** in the package.

    The census falls back to this index for a name it cannot bind in its own
    module — an `import`ed constant (`mcp_server` imports
    `SUPERSEDE_STRUCTURAL_RELS` from `sdk`). The uniqueness rule is what keeps
    the fallback from guessing between two different constants that happen to
    share a name: an ambiguous name resolves to nothing and is reported by the
    completeness arm as an undeclared empty-pattern site until it is declared.
    """
    seen: dict[str, list[tuple[ast.AST, _File]]] = {}
    for file in _literal_files():
        for name, value in file.index.consts.items():
            seen.setdefault(name, []).append((value, file))
    return {name: hits[0] for name, hits in seen.items() if len(hits) == 1}


@lru_cache(maxsize=1)
def _attr_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Class-level constants defined exactly once (e.g. `_ABOUT_TYPES`)."""
    seen: dict[str, list[tuple[ast.AST, _File]]] = {}
    for file in _literal_files():
        for attrs in file.index.classes.values():
            for name, value in attrs.items():
                seen.setdefault(name, []).append((value, file))
    return {name: hits[0] for name, hits in seen.items() if len(hits) == 1}


def _own_assigns(fn: ast.AST) -> dict[str, ast.AST]:
    """Assignments made directly in a function body (nested scopes excluded)."""
    out: dict[str, ast.AST] = {}

    def visit(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        out.setdefault(target.id, stmt.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and stmt.value is not None
                and isinstance(stmt.target, ast.Name)
            ):
                out.setdefault(stmt.target.id, stmt.value)
            for _field, value in ast.iter_fields(stmt):
                if isinstance(value, list) and value and all(
                    isinstance(item, ast.stmt) for item in value
                ):
                    visit(value)

    visit(fn.body)
    return out


def _param_defaults(fn: ast.AST) -> dict[str, ast.AST]:
    """A function's parameter DEFAULTS, by name.

    `link_entity(..., edge_type="aboutObject")` interpolates the *parameter*, not
    a local, and the default is where its value lives when every caller takes it
    — which is the case for the whole default capture path. Without this table
    the existence pre-probe that reads the edge on the write path is invisible
    even though the edge name is spelled one line above the query.
    """
    out: dict[str, ast.AST] = {}
    args = fn.args  # type: ignore[attr-defined]
    positional = list(args.posonlyargs) + list(args.args)
    paired = positional[len(positional) - len(args.defaults):]
    for arg, default in zip(paired, args.defaults, strict=True):
        out.setdefault(arg.arg, default)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if default is not None:
            out.setdefault(arg.arg, default)
    return out


def _scope(file: _File, node: ast.AST | None) -> dict[str, dict[str, ast.AST]]:
    """The bindings visible at `node`: loop vars, locals, params, attrs, globals.

    Innermost-first on every table: a loop variable shadows a local of the same
    name, which shadows a parameter default, which shadows the class attribute,
    which shadows the module global.
    """
    scope: dict[str, dict[str, ast.AST]] = {
        "loop": {},
        "local": {},
        "param": {},
        "cls": {},
        "module": file.index.consts,
    }
    if node is None:
        return scope
    seen_fn = False
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.For) and isinstance(cur.target, ast.Name):
            scope["loop"].setdefault(cur.target.id, cur.iter)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and not seen_fn:
            seen_fn = True
            for name, value in file.own_assigns(cur).items():
                scope["local"].setdefault(name, value)
            scope["param"] = _param_defaults(cur)
        if isinstance(cur, ast.ClassDef):
            scope["cls"] = {**file.index.classes.get(cur.name, {}), **scope["cls"]}
        cur = file.parents.get(cur)
    return scope


def _product(left: list[str], right: list[str]) -> list[str]:
    out: list[str] = []
    for head in left:
        for tail in right:
            out.append(head + tail)
            if len(out) >= _MAX_CANDIDATES:
                return out
    return out


def _resolve(
    node: ast.AST,
    scope: dict[str, dict[str, ast.AST]],
    file: _File,
    depth: int = 0,
) -> list[str]:
    """Every concrete string this expression can evaluate to.

    Unresolvable leaves contribute `_UNKNOWN` (a gap) rather than nothing, so a
    query that interpolates a value this resolver cannot follow still derives
    from the Cypher around it — the alternative (dropping the whole candidate)
    is exactly the literal-only blindness this function exists to remove.
    """
    if depth > 8:
        return [_UNKNOWN]
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else [_UNKNOWN]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[str] = []
        for element in node.elts:
            out.extend(_resolve(element, scope, file, depth + 1))
        return out[:_MAX_CANDIDATES]
    if isinstance(node, ast.JoinedStr):
        candidates = [""]
        for part in node.values:
            inner = part.value if isinstance(part, ast.FormattedValue) else part
            candidates = _product(candidates, _resolve(inner, scope, file, depth + 1))
        return candidates
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _product(
            _resolve(node.left, scope, file, depth + 1),
            _resolve(node.right, scope, file, depth + 1),
        )
    if isinstance(node, ast.Name):
        if node.id in scope["loop"]:
            return _resolve(scope["loop"][node.id], scope, file, depth + 1)
        for table in ("local", "param", "cls", "module"):
            bound = scope[table].get(node.id)
            if bound is not None:
                return _resolve(bound, scope, file, depth + 1)
        imported = _module_consts().get(node.id)
        if imported is not None:
            value, owner = imported
            return _resolve(value, _scope(owner, None), owner, depth + 1)
        return [_UNKNOWN]
    if isinstance(node, ast.Attribute):
        attr = _attr_consts().get(node.attr)
        if attr is not None:
            value, owner = attr
            return _resolve(value, _scope(owner, None), owner, depth + 1)
        return [_UNKNOWN]
    if isinstance(node, ast.Call):
        # `frozenset({...})` / `set({...})` / `tuple([...])` / `list([...])` over
        # a resolvable member list — `frozenset` is the IDIOMATIC shape for a rel
        # family in this package (`DERIVABLE_STRUCTURAL_RELS`, `_ABOUT_REPLAY_RELS`,
        # `ENTITY_LINKED_RELS`), and a `for rel in CONSTANT` loop over one is the
        # same read as a loop over a tuple. Without this branch those loops fell
        # through to the gap and the family constants escaped the census.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in ("frozenset", "set", "tuple", "list")
            and len(node.args) == 1
        ):
            return _resolve(node.args[0], scope, file, depth + 1)
        # `"|".join(ABOUT_EDGE_TYPES)` — the other call shape that builds a rel
        # pattern: a separator constant joined over a constant's members.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
        ):
            separators = _resolve(node.func.value, scope, file, depth + 1)
            members = _resolve(node.args[0], scope, file, depth + 1)
            return [sep.join(members) for sep in separators][:_MAX_CANDIDATES]
        return [_UNKNOWN]
    if isinstance(node, ast.IfExp):
        return (
            _resolve(node.body, scope, file, depth + 1)
            + _resolve(node.orelse, scope, file, depth + 1)
        )[:_MAX_CANDIDATES]
    return [_UNKNOWN]


def _may_traverse_every_type(source: str) -> bool:
    """Text pre-filter for the empty-pattern class: is there a relationship
    pattern with no type in it?

    Both spellings count: the bracketed `-[r]->` (no type slot at all) and the
    bracket-less `(n)--()`. Cheap, and only a filter — `_is_empty_pattern_read`
    decides. It must stay a SUPERSET of the class, or a filtered-out file is a
    reader that ships green — which is exactly how the bracket-less member was
    missed while `EMPTY_PATTERN` was floor-checked.
    """
    if any(_type_slot(m.group(1)) is None for m in _EMPTY_TRAVERSAL.finditer(source)):
        return True
    return bool(_BARE_TRAVERSAL.search(source))


def _may_have_gap_type(source: str) -> bool:
    """Text pre-filter for the call-time class: is any type slot an EXPRESSION?

    Also only a filter (`_is_gap_type_read` decides) — but it has to be a
    SUPERSET of the class, or the walk never reaches the site it exists for: a
    filtered-out file is a reader that ships green.
    """
    return bool(_GAP_TYPE_HINT.search(source))


def _docstrings(file: _File) -> set[int]:
    """The `id()`s of every module/class/function docstring Constant.

    A docstring is prose that may QUOTE Cypher (this package's docs do, e.g. a
    "tried `MATCH (n)<-[r]-(m)` and it scans" note), so it is not a site: only an
    executed string literal is. Without this, the class walk would derive a
    function whose docstring merely mentions the pattern.
    """
    found: set[int] = set()
    for node in ast.walk(file.tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return found


def _is_read(text: str) -> bool:
    """Does this Cypher string traverse the edge under a reading clause?

    Per traversal, the *last* governing clause before it decides: `MATCH`/
    `OPTIONAL MATCH` read, `MERGE`/`CREATE` mint.
    """
    for match in _TRAVERSAL.finditer(text):
        clauses = _CLAUSE.findall(text[: match.start()])
        if not clauses:
            continue
        governing = " ".join(clauses[-1].split()).upper()
        if governing in ("MATCH", "OPTIONAL MATCH"):
            return True
    return False


def _type_slot(content: str) -> str | None:
    """What TYPE a bracketed traversal names, decided once for both shapes.

    The type slot is the text after the first `:` in the part BEFORE any property
    map — a colon inside `{...}` is a map key, not a type (which is how
    `MATCH (n)-[r {idx:$i}]->(m)` names no type while carrying a colon).

    * `None` — the bracket carries no type slot at all: `[r]`, `<-[r]-`,
      `[r*1..2]`, `[r {idx:$i}]`. It walks EVERY relationship type.
    * `""` — the slot exists and is EMPTY: `[r:]`, `[:]`, `[r: {c:$c}]`. That is
      what an interpolation the resolver could not fill leaves behind, so this is
      the call-time class's signature.
    * anything else — the type the pattern NAMES (`aboutObject`, `A|B`).
    """
    head = content.split("{", 1)[0]
    if ":" not in head:
        return None
    return re.split(r"[{*\s]", head.split(":", 1)[1].strip(), maxsplit=1)[0]


def _reads_at(text: str, start: int) -> bool:
    """Is the traversal at `start` governed by a reading clause?

    Per traversal, the *last* clause before it decides: `MATCH`/`OPTIONAL MATCH`
    read, `MERGE`/`CREATE` mint. Shared by both shape walks, so neither can drift
    from the other (or from `_is_read`).
    """
    clauses = _CLAUSE.findall(text[:start])
    if not clauses:
        return False
    return " ".join(clauses[-1].split()).upper() in ("MATCH", "OPTIONAL MATCH")


def _is_empty_pattern_read(text: str) -> bool:
    """Does this Cypher traverse a relationship it does NOT name?

    Same governing-clause rule as `_is_read` — only a `MATCH`/`OPTIONAL MATCH`
    makes it a read — but the subject is the pattern's SHAPE: a traversal with no
    type slot at all (`-[r]->`, `<-[r]-`, `-[r {c:$c}]->`, and their bracket-less
    spelling `(n)--()`) has no type, so it walks every relationship type and
    `aboutObject` is one of them. Both spellings are checked, because a reader
    written in the bracket-less one is the same read and was invisible here.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if _type_slot(match.group(1)) is not None:
            continue
        if _reads_at(text, match.start()):
            return True
    return any(_reads_at(text, match.start()) for match in _BARE_TRAVERSAL.finditer(text))


def _is_gap_type_read(text: str) -> bool:
    """Does this Cypher traverse a relationship whose type is a GAP?

    The third shape, and the one no name-based scan can see: the type slot is
    filled by an expression (a parameter, a dict lookup, a journal field), which
    `_resolve` follows one level and then leaves empty — `-[:{rel}]->` resolves to
    `-[:]->`. A bracketed traversal with an empty type slot is therefore the
    signature of a call-time relationship name, and `aboutObject` may be what the
    expression evaluates to at run time.

    A literal `[r:]` would be invalid Cypher, so an empty slot on a resolved
    candidate is always an interpolation the resolver could not fill.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if _type_slot(match.group(1)) != "":
            continue
        if _reads_at(text, match.start()):
            return True
    return False


def _roots(file: _File) -> list[ast.AST]:
    """The outermost string expressions — the unit a site is derived from.

    One Cypher statement is often several AST nodes: adjacent literals and
    f-string parts concatenate into a `BinOp`/`JoinedStr`, so descending to each
    `Constant` would split a statement and see neither the clause nor the
    relationship name. Only the outermost node is a root; its parts are joined
    by `_resolve`.
    """
    roots: list[ast.AST] = []
    for node in ast.walk(file.tree):
        parent = file.parents.get(node)
        inside_concat = isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, str):
                continue
            if isinstance(parent, (ast.JoinedStr, ast.FormattedValue)) or inside_concat:
                continue
            roots.append(node)
        elif (
            isinstance(node, ast.JoinedStr)
            or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add))
        ) and not inside_concat:
            roots.append(node)
    return roots


def _site_symbol(node: ast.AST, file: _File) -> list[str]:
    """The symbol(s) a Cypher expression belongs to.

    The innermost function/method is the site; a module-level constant is
    attributed to the function(s) that consume it, because that is where the
    read happens (`subgraph._CYPHER_ABOUT` is executed by `_collect_raw`).
    """
    chain: list[str] = []
    cur = file.parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(cur.name)
        cur = file.parents.get(cur)
    if chain:
        # innermost function wins; a `Klass.method` qualifier is a convenience
        # path, not a symbol (the same rule `citation_rot` applies).
        return [chain[0]]

    const_name = None
    for stmt in file.tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if any(target is node for target in ast.walk(stmt.value)):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    const_name = target.id
    if const_name is None:
        return ["<module>"]

    return file.const_users().get(const_name, []) or [f"<module:{const_name}>"]


def _sites_in(file: _File) -> set[tuple[str, int]]:
    """`{(symbol, line)}` — every READ site this file derives.

    NO text gate of its own. `_files()` already admitted this module — on the
    edge's name, a rel-family constant, an every-type pattern, or a gap type —
    and re-testing the first two predicates here re-imposed on the walk exactly
    the blindness `_files()` exists to refute: a module admitted BECAUSE OF ITS
    SHAPE was parsed and then deliberately not walked. The live shape such a gate
    drops is a constant FRAGMENT interpolated into the type slot
    (`_REL = "about"` -> `f"...[:{_REL}Object]..."`), which `_resolve` follows
    and the gate hid — against §13's own promise that a reader *"that names the
    edge — literally, or through a constant the resolver can follow — reds the
    guard"*. It also made the superset arm unfalsifiable: `test_the_file_filter_
    un_sees_nothing` re-uses THIS walk as its proof, so a gate narrowing here was
    invisible to it as well.

    Docstrings are skipped for the reason `_docstrings()` states — prose that
    QUOTES a pattern is not an executed read. The empty-pattern and call-time
    walks both apply that rule; this one did not, so a docstring quoting
    `MATCH (n)-[:aboutObject]->(m)` would have demanded a §13 row for a comment.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(EDGE in text and _is_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


@lru_cache(maxsize=1)
def _rel_constant_names() -> frozenset[str]:
    """Constants whose own value carries the edge name.

    The census's file filter: a module can build its rel pattern out of
    `SUPERSEDE_STRUCTURAL_RELS` without ever writing the literal, so reading only
    files that contain `aboutObject` would reproduce the blindness one level up.
    """
    names: set[str] = set()
    for table in (_module_consts(), _attr_consts()):
        for name, (value, owner) in table.items():
            if any(EDGE in text for text in _resolve(value, _scope(owner, None), owner)):
                names.add(name)
    return frozenset(names)


@lru_cache(maxsize=1)
def _census() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site under `tortoise/` whose Cypher traverses the edge.

    `{(relative_path, symbol): (line numbers)}` — READ sites only.

    Cached: three tests read it and it parses the whole package with `ast`.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        for symbol, line in sorted(_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(value) for key, value in sites.items()}


@lru_cache(maxsize=1)
def _empty_pattern_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site that reads the edge through a pattern naming NO type.

    A separate walk from `_census()` because the site is not identified by a
    name at all — the class is derived from the SHAPE of the pattern. Kept apart
    so the completeness arm can say which class a missing site belongs to, and
    so the class's own declaration (`EMPTY_PATTERN`) is checked against what the
    code actually does rather than against itself.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        if not _may_traverse_every_type(file.source):
            continue
        docstrings = _docstrings(file)
        for node in _roots(file):
            if id(node) in docstrings:
                continue
            candidates = _resolve(node, _scope(file, node), file)
            if not any(_is_empty_pattern_read(text) for text in candidates):
                continue
            line = getattr(node, "lineno", 0)
            for symbol in _site_symbol(node, file):
                sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}


def _call_time_sites_in(file: _File) -> set[tuple[str, int]]:
    """`_call_time_sites()` restricted to one (usually synthetic) module.

    The mechanism arm's harness: it lets a synthetic source — a reader of a shape
    the tree does not contain — be run through the SAME derivation the real
    census uses, so "a new member of this class cannot ship green" is measured on
    a source that would be a new member, not on today's tree.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(_is_gap_type_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


@lru_cache(maxsize=1)
def _call_time_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site that reads an edge whose relationship TYPE is an expression.

    The third class, and the one the census used to DECLARE instead of derive: a
    relationship name assembled at call time (a parameter, a dict lookup, a
    journal field) leaves the type slot empty when the resolver gives up on it, so
    the shape `-[r:]->` is the class's signature — exactly as `-[r]->` is the
    empty-pattern class's. Deriving it is what makes a NEW reader of the shape red
    instead of shipping green: a hand list can only check the entries it was
    given, which is how `traverse`, `ingest` and the replay reads were missed.

    Kept apart from the other two walks because each class answers a different
    question, and a missing site has to be reported against the class it belongs
    to ("this names no type" and "this names a type nobody can see" are
    different defects with different fixes).
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        if not _may_have_gap_type(file.source):
            continue
        for symbol, line in sorted(_call_time_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}


@lru_cache(maxsize=1)
def _declared_rows() -> tuple[tuple[int, str, str, tuple[tuple[str, str], ...]], ...]:
    """§13's table, parsed: `(row_number, site_cell, reach_cell, site_pairs)`.

    The document is the declaration — there is no second manifest to drift.
    """
    lines = STORAGE.read_text(encoding="utf-8").splitlines()
    rows: list[tuple[int, str, str, tuple[tuple[str, str], ...]]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not in_table:
            if stripped.startswith(_TABLE_HEADER):
                in_table = True
            continue
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        number, site_cell, reach_cell = int(cells[0]), cells[1], cells[3]
        rows.append((number, site_cell, reach_cell, _row_site_pairs(site_cell)))
    return tuple(rows)


def _row_site_pairs(site_cell: str) -> tuple[tuple[str, str], ...]:
    """The `(basename, base_symbol)` pairs one `site` cell actually claims.

    Built **positionally**: symbols following a file token belong to that file,
    so a row citing two files with two differently-owned symbols cannot satisfy
    four pairings the row never meant (the cross-product loosening). The table
    is prose, so this is the one mechanical pairing available — and it is
    exact, which is what an arm whose whole purpose is "an omitted reader
    cannot pass" needs.

    Line numbers are never asserted (see the docstring), so a row may carry one
    or omit it. Symbols are the backticked tokens that are not a filename and
    not an issue reference.
    """
    pairs: list[tuple[str, str]] = []
    current: str | None = None
    for raw in re.findall(r"`([^`]+)`", site_cell):
        token = raw.strip()
        file_match = re.fullmatch(r"([A-Za-z0-9_/]+\.py)(?::\d+)?", token)
        if file_match:
            current = file_match.group(1).rsplit("/", 1)[-1]
            continue
        if token.startswith("#") or ":" in token or not _SYMBOL.match(token):
            continue
        if current is None:
            # A symbol with no file before it names no site.
            continue
        pairs.append((current, _last(token)))
    return tuple(pairs)


def _last(symbol: str) -> str:
    """The base symbol: `ranking.SubgraphExpander._neighbors` -> `_neighbors`."""
    return re.sub(r"\(\)$", "", symbol).rsplit(".", 1)[-1]


def _numeral(token: str) -> int | None:
    """`SIX` -> 6, `20` -> 20, a word this table does not know -> `None`.

    Returning `None` rather than dropping the token is deliberate: a count the
    arm cannot read must be reported, because silently skipping it is how an
    unreadable number becomes an unguarded one.
    """
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


@lru_cache(maxsize=1)
def _bullet_text() -> str:
    """§13's `aboutObject` bullet, verbatim — the passage the ruling rests on.

    Extracted once so every arm that reads the prose agrees on what "the census
    prose" is: the bullet starts at its own first line and ends at the next
    top-level bullet, so the section's other numerals (a `123`-hub cap) cannot
    be read as census claims.
    """
    bullet: list[str] = []
    inside = False
    for line in STORAGE.read_text(encoding="utf-8").splitlines():
        if not inside:
            inside = line.startswith(_BULLET_START)
            continue
        if line.startswith("- **") or line.startswith("---"):
            break
        bullet.append(line)
    return "\n".join(bullet)


def _parse_prose_counts(text: str) -> dict[str, tuple[str, ...]]:
    """The four unit-keyed count tokens in `text`, verbatim, in reading order.

    Split out from `_prose_counts()` so the sentence forms can be exercised on
    synthetic prose (`test_the_numeral_parsing_accepts_digits`) instead of only
    against whatever the document happens to say today — the digits arm had no
    such seam, which is why its unreachability went unnoticed.
    """
    callable_rows = [m.group(1) for m in _PROSE_CALLABLE_ROWS.finditer(text)]
    callable_rows += [m.group(1) for m in _PROSE_CALLABLE.finditer(text)]
    callable_rows += [m.group(1) for m in _PROSE_CALLABLE_AGAIN.finditer(text)]
    return {
        "sites": tuple(m.group(1) for m in _PROSE_SITES.finditer(text)),
        "rows": tuple(m.group(1) for m in _PROSE_ROWS.finditer(text)),
        "callable_rows": tuple(callable_rows),
        "callable_sites": tuple(m.group(2) for m in _PROSE_CALLABLE_ROWS.finditer(text)),
    }


def _chain_increments(text: str) -> tuple[tuple[str, str, str], ...]:
    """The change chain's `<from> → <to> <unit>` increments, as written."""
    return tuple(
        (m.group(1), m.group(2), m.group(3).lower())
        for m in _PROSE_CHAIN.finditer(text)
    )


@lru_cache(maxsize=1)
def _prose_counts() -> dict[str, tuple[str, ...]]:
    """§13's asserted counts, verbatim, keyed by the UNIT each one counts.

    `sites` / `rows` / `callable_rows` / `callable_sites` are different numbers,
    and the headline used to mix the first and the third under one "of them".
    The tokens are returned as written, not as numbers, so the arm can name the
    sentence's own word when it disagrees with the table.
    """
    return _parse_prose_counts(_bullet_text())


def test_the_table_is_not_vacuous():
    """Every arm below must be able to find something, or all of them pass by
    finding nothing — the failure mode that let four hand audits conclude the
    list was complete."""
    sites = _census()
    empty_pattern = _empty_pattern_sites()
    call_time = _call_time_sites()
    rows = _declared_rows()
    customer_callable = [row for row in rows if "customer-callable" in row[2].lower()]

    assert len(sites) >= _MIN_SITES, (
        f"the census derived only {len(sites)} read sites from tortoise/ — the "
        "traversal shape changed (or the parser broke) and the completeness arm "
        "would pass vacuously. Sites: "
        + ", ".join(sorted(f"{f}::{s}" for f, s in sites))
    )
    assert len(empty_pattern) >= _MIN_EMPTY_PATTERN, (
        "the empty-pattern walk derived only "
        f"{len(empty_pattern)} sites from tortoise/ — the shape detector broke, "
        "and with it the only arm that can see a reader which names no "
        "relationship type. The defect that produced this arm was exactly this "
        "class going unenumerated."
    )
    assert len(call_time) >= _MIN_CALL_TIME, (
        "the call-time walk derived only "
        f"{len(call_time)} sites from tortoise/ — the type-slot detector broke, "
        "and with it the only arm that can see a relationship type written as an "
        "expression. While it was a hand list, `TortoiseSDK.traverse` and "
        "`TortoiseSDK.ingest` were outside the census and every arm was green."
    )
    assert len(rows) >= _MIN_ROWS, (
        f"§13's table parsed to {len(rows)} rows — the table moved out of the "
        "shape this guard reads, so nothing below is being checked."
    )
    assert len(customer_callable) >= _MIN_CUSTOMER_CALLABLE, (
        "§13 must name at least the customer-callable surfaces the KEEP ruling "
        "rests on; the table declares "
        f"{len(customer_callable)}."
    )
    # The class register is the only hand-maintained list in the file now, so it
    # must not quietly empty out (its members are checked against the code).
    assert EMPTY_PATTERN, (
        "the empty-pattern register is empty — either every member is a §13 row "
        "(delete the register and this arm) or the class's out-of-scope members "
        "were dropped without a replacement, which is how three of them went "
        "missing while every arm stayed green."
    )


def test_the_prose_counts_match_the_table():
    """The NUMBERS are the argument, so they are asserted, not narrated.

    §13 states its own totals in prose, and the KEEP ruling rests on the
    customer-callable one. The arms above guard the **rows**; nothing guarded
    the **numerals**, so a row demoted in the `reach` column left every test
    green while the headline still said SIX. That is the `#7956` erratum's
    direction (the count was under-stated), and it is the error this table
    exists to prevent.

    ⚠️ **Each numeral is checked against its own unit.** The headline used to
    read *"TWENTY read sites, and SIX of them are CUSTOMER-CALLABLE"*: TWENTY
    counted backticked SYMBOLS, SIX counted ROWS, and the six customer-callable
    rows carried nine symbols — so "of them" was false by three, and the arm
    baked the mismatch in by comparing each numeral to a different set. Sites,
    rows, customer-callable rows and the sites inside them are now four
    separate claims, each compared to the set it names.

    Only the numerals are read — never the prose around them — so the sentences
    stay free to be rewritten as long as they stay true.
    """
    counts = _prose_counts()
    rows = _declared_rows()
    declared_sites = {pair for _n, _cell, _reach, pairs in rows for pair in pairs}
    customer_callable = [row for row in rows if "customer-callable" in row[2].lower()]
    callable_sites = {
        pair for _n, _cell, _reach, pairs in customer_callable for pair in pairs
    }

    #: `(key, what it counts, the set it must equal, the sentence form)` — the
    #: four units, so a rewrite cannot state one where the other belongs.
    claims: tuple[tuple[str, str, int, str], ...] = (
        ("sites", "read sites", len(declared_sites), "`<N> read sites`"),
        ("rows", "rows", len(rows), "`<N> rows`"),
        (
            "callable_rows",
            "customer-callable rows",
            len(customer_callable),
            "`<N> of those rows (<M> sites) are CUSTOMER-CALLABLE`",
        ),
        (
            "callable_sites",
            "sites inside the customer-callable rows",
            len(callable_sites),
            "`<N> of those rows (<M> sites) are CUSTOMER-CALLABLE`",
        ),
    )

    problems: list[str] = []
    for key, unit, actual, form in claims:
        tokens = counts[key]
        if not tokens:
            problems.append(
                f"§13 no longer states its {unit} count ({form}), so the number "
                "this arm polices is unguarded again (restore the sentence, or "
                "delete this arm and say why in the docstring)"
            )
            continue
        for token in tokens:
            stated = _numeral(token)
            if stated is None:
                problems.append(
                    f"§13 states `{token}` {unit} and this guard cannot read "
                    "that numeral — spell it out in words or digits"
                )
            elif stated != actual:
                problems.append(
                    f"§13 states `{token}` {unit}; the table has {actual} "
                    f"({form})"
                )

    assert not problems, (
        "the counts §13 asserts do not match the table it asserts them about. "
        "⚠️ The customer-callable number IS the argument for keeping the edge — "
        "a stale numeral here is the ruling resting on an unmeasured claim, and "
        "a numeral checked against the WRONG unit is the same claim with a "
        "cleaner alibi:\n  - "
        + "\n  - ".join(problems)
    )


def test_the_change_chain_ends_at_the_headline_counts():
    r"""The chronology must ARRIVE at the total the headline states.

    §13 carries the table, the headline numeral, and then the chronology that
    explains *why* the count moved — eleven → fifteen → twenty → twenty-two. At
    the head that added rows 19 and 20 (`session_link.link_entity`,
    `subject_binding.bind_point_subjects`) the headline said TWENTY-FOUR and the
    chain still ended at twenty-two: the paragraph whose whole purpose is to
    explain the number disagreed with the number, in the one passage this guard
    exists to make reconcilable. `test_the_prose_counts_match_the_table` cannot
    see it — the history numerals (`eleven`, `fifteen`, `twenty`, `twenty-two`)
    match none of the four asserted sentence forms, and must not, because an
    increment is a historical claim rather than a total.

    So the LAST increment of each unit is compared to the table: whatever the
    history was, the chain has to end where the table is. That is the one thing
    every increment's wording is free to change without making the narration
    false, and it is the arm that was missing.
    """
    text = _bullet_text()
    assert text, (
        "§13's `aboutObject` bullet was not found, so this arm passes vacuously "
        "— re-point `_BULLET_START` at the passage that carries the census"
    )
    rows = _declared_rows()
    declared_sites = {pair for _n, _cell, _reach, pairs in rows for pair in pairs}
    actual = {"sites": len(declared_sites), "rows": len(rows)}

    increments = _chain_increments(text)
    problems: list[str] = []
    for unit in ("sites", "rows"):
        of_unit = [increment for increment in increments if increment[2] == unit]
        if not of_unit:
            problems.append(
                f"§13's change chain states no `<from> → <to> {unit}` increment "
                "at all, so nothing narrates why the count moved — and the "
                f"table states {actual[unit]} {unit} with no chronology behind it"
            )
            continue
        _from, to, _unit = of_unit[-1]
        stated = _numeral(to)
        if stated is None:
            problems.append(
                f"the chain's last {unit} increment ends at `{to}`, a numeral "
                "this guard cannot read — spell it out in words or digits"
            )
        elif stated != actual[unit]:
            problems.append(
                f"§13's change chain stops at `{to}` {unit} while its table has "
                f"{actual[unit]} — the paragraph explaining the count narrates a "
                "different number from the count it explains (add the closing "
                "increment, or correct the numeral)"
            )

    assert not problems, (
        "§13's chronology no longer reconciles with §13's table. This passage "
        "exists to answer \"why is the number what it is\", so a chain that stops "
        "short answers it with a smaller number — the reconciliation failure this "
        "whole guard was written to end, one paragraph up from the table:\n  - "
        + "\n  - ".join(problems)
    )


def test_the_numeral_parsing_accepts_digits():
    r"""Both numeral SPELLINGS parse — the digits branch is reachable.

    `_numeral` promised *"digits are accepted as well, so a rewrite that writes
    `6` instead of `SIX` stays checkable"* while every capture group was
    `([A-Za-z][A-Za-z-]*)`, so `token.isdigit()` was dead code and a digit
    rewrite of §13's headline matched **nothing**: the count arm then redded with
    *"§13 no longer states its site count … restore the sentence"*, a false
    diagnosis naming a cause that is not there. It failed closed, but an
    instrument that cannot answer its own question is exactly the shape this
    file exists to end.

    Non-vacuity is measured rather than assumed: each sentence form is fed BOTH
    spellings of the SAME claim and both must parse to the same number — so this
    arm reds if the captures go back to words-only, and it reds if a form is
    dropped. It also pins the chain form (`<from> → <to> <unit>`), because that
    is the form the change-chain arm reads.
    """
    word_form = (
        "TWENTY-FOUR read sites across TWENTY rows, and SEVEN of those rows "
        "(ten sites) are CUSTOMER-CALLABLE — seven of them are customer-callable"
    )
    digit_form = (
        "24 read sites across 20 rows, and 7 of those rows (10 sites) are "
        "CUSTOMER-CALLABLE — 7 of them are customer-callable"
    )
    expected = {"sites": 24, "rows": 20, "callable_rows": 7, "callable_sites": 10}

    problems: list[str] = []
    for spelling, text in (("words", word_form), ("digits", digit_form)):
        counts = _parse_prose_counts(text)
        for key, value in expected.items():
            tokens = counts[key]
            if not tokens:
                problems.append(
                    f"the {spelling} spelling of the `{key}` sentence did not "
                    "parse at all — the capture no longer accepts that numeral "
                    "form, so a rewrite in it would red with the wrong cause"
                )
                continue
            for token in tokens:
                if _numeral(token) != value:
                    problems.append(
                        f"`{token}` ({spelling}, {key}) reads as "
                        f"{_numeral(token)}, expected {value}"
                    )

    word_chain = _chain_increments("moved — twenty-two → twenty-four sites and eighteen → twenty rows")
    digit_chain = _chain_increments("moved — 22 → 24 sites and 18 → 20 rows")
    if word_chain != (
        ("twenty-two", "twenty-four", "sites"),
        ("eighteen", "twenty", "rows"),
    ):
        problems.append(
            "the chain form no longer parses its word spelling: "
            f"{word_chain!r}"
        )
    if digit_chain != (("22", "24", "sites"), ("18", "20", "rows")):
        problems.append(
            f"the chain form no longer parses digits: {digit_chain!r}"
        )
    for chain in (word_chain, digit_chain):
        for _from, to, unit in chain:
            if _numeral(to) is None:
                problems.append(f"the chain's `{to}` {unit} does not read as a number")

    assert not problems, (
        "the census's numeral parsing no longer accepts every spelling it "
        "documents, so a legitimate rewrite reds as a missing sentence:\n  - "
        + "\n  - ".join(problems)
    )


def test_the_empty_pattern_register_count_matches_the_prose():
    r"""`(<N> members today` is a numeral like the rest, so it is asserted.

    §13 states the size of the guard's `EMPTY_PATTERN` register in the sentence
    that declares the empty-pattern class — `(<N> members today, with the reason
    each is not an `aboutObject` read for a referent)` — and THAT sentence is the
    only place the number lives: this docstring quoted its own copy, which went
    stale the moment the register grew, while the arm went on asserting the
    document's. That was the ONE
    census-adjacent numeral in the bullet nothing read: `test_the_table_is_not_vacuous`
    only floors the register (`assert EMPTY_PATTERN`), so adding or removing a
    member — the hand-maintained list this file's other arms exist to keep honest
    — falsified the sentence silently. Same rule as the headline counts: a numeral
    is a claim, and it is compared to the set it counts.
    """
    text = _bullet_text()
    tokens = [m.group(1) for m in _PROSE_EMPTY_PATTERN.finditer(text)]
    assert tokens, (
        "§13 no longer states the `EMPTY_PATTERN` register's size (`(<N> members "
        "today`), so this arm checks nothing — restore the clause or delete the "
        "arm and say why in the docstring"
    )
    problems = [
        f"§13 states `{token}` members and the register has {len(EMPTY_PATTERN)}"
        for token in tokens
        if _numeral(token) != len(EMPTY_PATTERN)
    ]
    assert not problems, (
        "§13's stated size of the empty-pattern register does not match the "
        "register itself:\n  - " + "\n  - ".join(problems)
    )


def test_every_code_site_that_traverses_the_edge_is_declared():
    """COMPLETENESS: all three derivations must be declared somewhere.

    This is the arm that no symbol-resolution check can replace — a *missing*
    row has no symbol to fail to resolve. It covers three classes:

    * a reader that NAMES the edge — literally, or through a constant the
      resolver can follow — must be a §13 row;
    * a reader that names NO type at all (`MATCH (n)-[r]->(m)`) must be either a
      §13 row or an `EMPTY_PATTERN` entry saying why it is not an `aboutObject`
      read for a referent. ⚠️ This class used to be ONE declared example, so the
      three further members that exist today — `entityProfile` / `tortoise_traverse`
      (customer-callable) and the backfill census — were absent from the table
      while every arm stayed green.
    * a reader whose type slot is an EXPRESSION the resolver cannot fill
      (`-[:{rel}]->`) must be a §13 row or a `CALL_TIME_REL` entry. ⚠️ This class
      used to be a hand list whose completeness was TRUSTED, so it could only
      ever check the entries it was given: two customer-callable SDK readers
      (`traverse`, `ingest`) sat outside the census, and a new reader of the
      shape would have shipped green.
    """
    declared = {
        pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs
    }
    registered = {(filename, _last(symbol)) for filename, symbol, _reason in EMPTY_PATTERN}
    registered |= {
        (filename, _last(symbol)) for filename, symbol, _why in CALL_TIME_REL
    }

    undeclared = []
    for label, sites in (
        ("names the edge", _census()),
        ("names no type (empty pattern)", _empty_pattern_sites()),
        ("names a type the resolver cannot follow (call time)", _call_time_sites()),
    ):
        for (path, symbol), lines in sorted(sites.items()):
            if (path.rsplit("/", 1)[-1], symbol) in declared:
                continue
            if (path, symbol) in registered:
                continue
            undeclared.append((label, path, symbol, lines))

    assert not undeclared, (
        "these sites traverse the `aboutObject` edge and §13's table neither "
        "names them nor declares them out of scope. That table is the evidence "
        "base for the KEEP ruling, and the document's own rule is that the "
        "count IS the argument — so an omitted reader is an unmeasured one "
        "(add the row, with the symbol, to `## 13` of STORAGE-ARCHITECTURE.md, "
        "or — only if it is not an about* read for a referent — add it to the "
        "register for its class, `EMPTY_PATTERN` or `CALL_TIME_REL`, with the "
        "reason):\n  - "
        + "\n  - ".join(
            f"{path}::{symbol} — {label} (lines {lines})"
            for label, path, symbol, lines in undeclared
        )
    )


def test_every_declared_symbol_still_performs_the_read():
    """ROT: a row whose symbol resolves but does not read is a false row.

    Four prior audits failed in this direction too — a symbol that exists, is
    quoted, and is not where the traversal happens — so the check is that the
    symbol is a **site of one of the three derivations**, not merely present in
    the file. A name-only check would pass `_hop_query` (which builds the query)
    for the wrong reason, so the three classes count and nothing else does.
    """
    site_symbols: set[tuple[str, str]] = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in _census()
    } | {(path.rsplit("/", 1)[-1], symbol) for path, symbol in _empty_pattern_sites()}
    site_symbols |= {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in _call_time_sites()
    }

    problems: list[str] = []
    for number, _site_cell, _reach, pairs in _declared_rows():
        for key in pairs:
            if key in site_symbols:
                continue
            problems.append(
                f"row {number}: `{key[1]}` ({key[0]}) is declared as a reader but no "
                "Cypher in it traverses `aboutObject` (by name or through an "
                "empty pattern)"
            )

    assert not problems, (
        "§13's table names these as readers of the `aboutObject` edge, and they "
        "are not — a re-verifier following the row finds no read and concludes "
        "the passage is wrong (the exact failure EXTRACTOR-V4-ARCHITECTURE.md's "
        "citation guard exists for, one document over):\n  - "
        + "\n  - ".join(problems)
    )


def test_the_empty_pattern_class_is_declared():
    """The class that names NO type is DERIVED, and every member is declared.

    Two directions, because each one was a real defect:

    * **every derived member is declared** — a NEW empty-pattern reader cannot
      appear silently. Declaring one example by name (`_neighbors`) is what let
      `entityProfile`/`tortoise_traverse` and the backfill census stay absent.
    * **every registered member still derives** — a register entry whose reader
      stopped traversing the pattern (renamed, rewritten to name types) is rot,
      and a stale entry is what makes the next reader's absence invisible.
    """
    derived = _empty_pattern_sites()
    row_symbols = {
        pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs
    }
    derived_keys = set(derived)

    missing = [
        f"{filename}::{symbol} — {reason}"
        for filename, symbol, reason in EMPTY_PATTERN
        if (filename, _last(symbol)) not in derived_keys
    ]
    assert not missing, (
        "these `EMPTY_PATTERN` entries are no longer empty-pattern readers of "
        "the edge, so the register has rotted (re-verify each: a renamed symbol "
        "or a query that now names its types):\n  - " + "\n  - ".join(missing)
    )

    registered = {(filename, _last(symbol)) for filename, symbol, _reason in EMPTY_PATTERN}
    about_reads = sorted(
        key
        for key in derived_keys
        if (key[0].rsplit("/", 1)[-1], key[1]) in row_symbols and key not in registered
    )
    assert len(about_reads) >= 3, (
        "the empty-pattern class has fewer than three members declared as §13 "
        "rows — the class's about*-relevant readers are `_neighbors`,\n"
        "  `_hop_query` (entityProfile / tortoise_traverse) and the backfill "
        "census; if one of them stopped deriving, the shape detector broke and "
        "this arm would pass over a missing row. Derived as rows: "
        + ", ".join(f"{f}::{s}" for f, s in about_reads)
    )


#: Synthetic sources for the mechanism arm below: the shapes a relationship name
#: reaches a query in. The fifth is a WRITER and must derive as nothing.
_MECHANISM_CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "literal",
        'def read(g):\n    g.query("MATCH (n)-[:aboutObject]->(m) RETURN n")\n',
        True,
    ),
    (
        "plus-concatenation of a constant",
        'REL = "aboutObject"\n\n\ndef read(g):\n'
        '    g.query("MATCH (n)-[:" + REL + "]->(m) RETURN n")\n',
        True,
    ),
    (
        "f-string of a constant",
        'REL = "aboutObject"\n\n\ndef read(g):\n'
        '    g.query(f"MATCH (n)-[:{REL}]->(m) RETURN n")\n',
        True,
    ),
    (
        "constant FRAGMENT interpolated into the type slot",
        '_REL = "about"\n\n\ndef read(g):\n'
        '    g.query(f"MATCH (n)-[:{_REL}Object]->(m) RETURN n")\n',
        True,
    ),
    (
        '`"|".join(TUPLE)`',
        'RELS = ("x", "aboutObject")\n\n\ndef read(g):\n'
        '    pattern = "|".join(RELS)\n'
        '    g.query("MATCH (n)-[r:" + pattern + "]->(m) RETURN n")\n',
        True,
    ),
    (
        "loop over a constant tuple",
        'RELS = ("x", "aboutObject")\n\n\ndef read(g):\n'
        "    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "parameter default",
        'def read(g, rel="aboutObject"):\n'
        '    g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "lowercase MATCH clause (Cypher keywords are case-insensitive)",
        'def read(g):\n    g.query("match (n)-[:aboutObject]->(m) RETURN n")\n',
        True,
    ),
    (
        "MERGE writer",
        'def write(g):\n    g.query("MERGE (n)-[:aboutObject]->(m) RETURN n")\n',
        False,
    ),
    (
        "lowercase merge writer (case-insensitivity must not turn a writer into a read)",
        'def write(g):\n    g.query("merge (n)-[:aboutObject]->(m) RETURN n")\n',
        False,
    ),
    (
        "the English verb `match` in prose is not a Cypher clause",
        'PROMPT = """\nIDENTITY MATCHING: match summary items to existing ones.\n'
        'Output: {"connections": [{"edge": "aboutObject|IMPL"}]}\n"""\n',
        False,
    ),
    (
        "a docstring quoting the named pattern is not a site",
        'def read(g):\n    """Tried `MATCH (n)-[:aboutObject]->(m)`; it works."""\n'
        "    return None\n",
        False,
    ),
)


def test_the_call_time_shape_is_derived_and_declared():
    """The THIRD class is DERIVED, and every member of it is declared.

    A relationship type written as an expression (a function argument, a dict
    lookup, a journal field) leaves the type slot EMPTY once the resolver gives
    up on it — `-[:{rel}]->` resolves to `-[:]->` — so the class has a SHAPE, and
    a shape can be walked. This arm used to be its opposite: a one-entry hand
    list (`UNDERIVABLE_CALL_TIME`) whose completeness was trusted, which could
    only ever validate the entries it was given. Measured against this head, that
    list was missing four live members — `TortoiseSDK.traverse` and
    `TortoiseSDK.ingest` (both customer-callable), plus the replay reads — and
    no arm could see a NEW member of the shape at all.

    Three directions, because each one has failed or could fail silently:

    * **every derived member is declared** — a §13 row, or a `CALL_TIME_REL`
      entry saying why its gap cannot carry `aboutObject` for a referent. A new
      reader of the shape therefore cannot ship green.
    * **every registered member still derives** — if a member stops being a
      call-time read (retargeted, or the resolver learns to follow it), the entry
      is rot and the class statement is wrong.
    * **the register is not the class** — the class has members that are §13
      rows, so an empty register is legitimate and must not make this arm vacuous
      (that vacuity is what the previous revision's "passes when the list is
      empty" defect looked like). The floor is on the DERIVED set.
    """
    derived = set(_call_time_sites())
    rows = {pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs}
    registered = {(filename, _last(symbol)) for filename, symbol, _why in CALL_TIME_REL}

    problems: list[str] = []
    for path, symbol in sorted(derived):
        if (path.rsplit("/", 1)[-1], symbol) in rows:
            continue
        if (path, symbol) in registered:
            continue
        problems.append(
            f"{path}::{symbol} reads an edge whose relationship type is an "
            "EXPRESSION (the resolved pattern carries an empty type slot), and "
            "nothing declares it — add a §13 row if `aboutObject` is reachable "
            "there for a referent, or a `CALL_TIME_REL` entry with the reason "
            "it is not"
        )

    for filename, symbol, _why in CALL_TIME_REL:
        target = ROOT / filename
        if not target.exists() or symbol not in target.read_text(encoding="utf-8"):
            problems.append(
                f"`{symbol}` no longer exists in {filename} — re-verify the entry"
            )
            continue
        if (filename, _last(symbol)) not in derived:
            problems.append(
                f"{filename}::{symbol} is registered in `CALL_TIME_REL` and the "
                "call-time walk no longer derives it — either it stopped reading "
                "through an expression (delete the entry) or the shape detector "
                "broke (fix it); a stale entry hides the next reader's absence"
            )

    assert len(derived) >= _MIN_CALL_TIME, (
        "the call-time walk derived "
        f"{len(derived)} sites, below the floor of {_MIN_CALL_TIME} — with no "
        "members, both checks above would pass over an empty set and the class "
        "would be unguarded again"
    )

    assert not problems, (
        "the class whose type slot is an expression is DERIVED from the resolved "
        "shape, so a member of it can only be legitimate as a reviewable "
        "declaration — a §13 row, or a `CALL_TIME_REL` entry with the reason its "
        "gap cannot carry `aboutObject` for a referent:\n  - "
        + "\n  - ".join(problems)
    )


#: Synthetic sources for the call-time arm: a type slot written as an
#: expression, in every shape the tree uses, plus the near-misses that must NOT
#: count (a named type, a writer, a docstring quoting the pattern) and the shapes
#: that belong to the OTHER classes (a bracket with no type slot at all, which is
#: the empty-pattern class's business).
_CALL_TIME_CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "f-string interpolation",
        'def read(g, rel):\n    g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "f-string interpolation with a property map",
        'def read(g, rel):\n'
        '    g.query(f"MATCH (n)-[:{rel} {{idx:0}}]->(m) RETURN n")\n',
        True,
    ),
    (
        "dict lookup from an event",
        'def read(g, ev):\n    rel = ev.get("edge_type")\n'
        '    g.query(f"MATCH (n)-[r:{rel}]->(m) DELETE r")\n',
        True,
    ),
    (
        "`+`-concatenated type slot",
        'def read(g, rel):\n'
        '    g.query("MATCH (n)-[r:" + rel + "]->(m) RETURN n")\n',
        True,
    ),
    (
        "interpolation into a bracketed pattern with no alias",
        'def read(g, rel):\n    g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "named type (the named census's job, not this class)",
        'def read(g):\n    g.query("MATCH (n)-[r:aboutObject]->(m) RETURN n")\n',
        False,
    ),
    (
        "empty pattern (the empty-pattern class's job, not this one)",
        'def read(g):\n    g.query("MATCH (n)-[r]->(m) RETURN type(r)")\n',
        False,
    ),
    (
        "MERGE writer",
        'def write(g, rel):\n    g.query(f"MERGE (n)-[:{rel}]->(m)")\n',
        False,
    ),
    (
        "a docstring quoting the pattern is not a site",
        'def read(g, rel):\n    """Tried `MATCH (n)-[:{rel}]->(m)`; it scanned."""\n'
        "    return None\n",
        False,
    ),
)


def test_the_call_time_shape_derivation_holds():
    """The MECHANISM of the third class, on synthetic sources.

    The class is only as good as its type-slot detector. These cases pin both
    directions on sources that do not depend on today's tree, including the two
    near-misses that must NOT count (a named type, a writer) and the two shapes
    that belong to the neighbouring classes (an empty pattern is the
    empty-pattern walk's, not this one) — two classes must not double-count one
    site and then each re-declare it.
    """
    missing = []
    for label, source, should_derive in _CALL_TIME_CASES:
        derived = _call_time_sites_in(
            _File(TORTISE / "<synthetic>", "<synthetic>", source)
        )
        if bool(derived) is not should_derive:
            missing.append(
                f"{label}: expected {'a site' if should_derive else 'no site'}, "
                f"derived {sorted(derived)}"
            )
    assert not missing, (
        "the call-time derivation lost a shape (or started counting one it must "
        "not), so a reader whose relationship type is an expression would pass "
        "this guard undeclared:\n  - " + "\n  - ".join(missing)
    )


#: Readers the tree does NOT contain, injected into the completeness arm's own
#: inputs. Non-vacuity for the class statement: each of these would be a NEW
#: member, and each must come back as undeclared — the property a hand list
#: cannot have.
_NEW_READER_CASES: tuple[tuple[str, str], ...] = (
    (
        "a relationship passed in as a parameter",
        'def read_param(g, rel):\n'
        '    g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
    ),
    (
        "a type read off a journal event",
        'def read_event(g, ev):\n    rel = ev.get("edge_type")\n'
        '    g.query(f"MATCH (n)-[r:{rel}]->(m) RETURN n")\n',
    ),
    (
        "a loop over a rel-family frozenset, via an f-string",
        "RELS = frozenset({'x', 'aboutObject'})\n\n\n"
        "def read_family(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
    ),
    (
        "a constant FRAGMENT interpolated into the type slot",
        '_REL = "about"\n\n\ndef read_fragment(g):\n'
        '    g.query(f"MATCH (n)-[:{_REL}Object]->(m) RETURN n")\n',
    ),
)


def _declared_keys() -> set[tuple[str, str]]:
    """Every declaration key, in ONE keyspace: `(module basename, base symbol)`.

    The two declaration surfaces were keyed DIFFERENTLY — §13's rows by BASENAME
    (`_row_site_pairs` -> `"sdk.py"`) and the registers by the FULL relative path
    (`"tortoise/sdk.py"`) — so the "already declared" branch of the arm below
    could not fire for a synthetic source under either spelling: the branch the
    docstring advertised as the second failure mode was dead code. This function
    folds both surfaces into §13's keyspace; the WALKS report `tortoise/<path>`,
    so the bridge between the two is the basename fold — `_basename_keys()` below
    is the one translator, and the arm requires the registers' keys to resolve
    into the walk keyspace through it.
    """
    keys = {pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs}
    keys |= _basename_keys(CALL_TIME_REL)
    keys |= _basename_keys(EMPTY_PATTERN)
    return keys


def _basename_keys(
    register: tuple[tuple[str, str, str], ...],
) -> set[tuple[str, str]]:
    """A register's entries in the rows' keyspace: `(basename, base symbol)`."""
    return {(filename.rsplit("/", 1)[-1], _last(symbol)) for filename, symbol, _why in register}


def _new_reader_problems(declared: set[tuple[str, str]]) -> list[str]:
    """Run every `_NEW_READER_CASES` source through the declaration predicate.

    Split out from the arm so the predicate can also be run against a
    DELIBERATELY declared synthetic key — that control is what proves the second
    failure mode is reachable, rather than a claim in a docstring.
    """
    problems: list[str] = []
    for label, source in _NEW_READER_CASES:
        file = _File(TORTISE / "<synthetic>", "<synthetic>", source)
        derived = set(_sites_in(file)) | _empty_pattern_sites_in(file)
        derived |= _call_time_sites_in(file)
        if not derived:
            problems.append(
                f"{label}: no derivation sees this reader at all, so it would "
                "ship as an unmeasured read"
            )
            continue
        for symbol, _line in sorted(derived):
            if ("<synthetic>", symbol) in declared:
                problems.append(
                    f"{label}: `{symbol}` is visible to a derivation AND already "
                    "declared — a synthetic source cannot be pre-declared, so this "
                    "means the declaration test is accepting anything and a new "
                    "member of the class would ship green"
                )
    return problems


def test_a_new_call_time_reader_cannot_ship_green():
    """NON-VACUITY of the class statement: a NEW member reds the completeness arm.

    The defect this arm exists for is not a wrong declaration, it is a missing
    one: while the class was a hand list, the arm could only ever validate the
    entries it was handed, so `TortoiseSDK.traverse` and `TortoiseSDK.ingest` sat
    outside the census with every test green. So the check is not "is today's
    tree declared" (the arm above does that) — it is "would tomorrow's reader be
    caught", measured on sources that are NOT in the tree.

    Each case is run through the shape walks and then through the completeness
    arm's own predicate: derived as SOME class (so the reader is visible at all)
    and declared by NO row or register (so it is a genuine new member). A case
    that a derivation cannot see fails here as loudly as one that is "declared".

    ⚠️ **The second failure mode is measured, not asserted.** It used to be
    unreachable — the declared-key sets were built in two different keyspaces, so
    no `("<synthetic>", symbol)` key could ever be a member — and an arm whose
    half cannot fire is a PASS about nothing. `_declared_keys()` puts both
    surfaces in the walks' keyspace, and the CONTROL below declares a synthetic
    key deliberately and requires the predicate to REPORT it: if that control
    stops firing, this arm's "declared" half is dead again and this test says so.
    """
    declared = _declared_keys()
    assert not _new_reader_problems(declared), (
        "a reader of a shape this PR claims to derive is not visible to the "
        "declaration test, which is exactly how the four missing members "
        "survived four reviews:\n  - "
        + "\n  - ".join(_new_reader_problems(declared))
    )

    # The registers must resolve into the WALK keyspace, not merely into the
    # declaration set this function just built — recomputing the same fold here
    # would only re-assert the definition. The walks report `tortoise/<path>`, a
    # register entry names one of its readers, so its basename key must BE one of
    # the derived sites; if a register drifts back to a full path (or names a
    # module the walks stopped deriving), that entry stops resolving and this
    # reds. That drift is what made the "already declared" branch dead.
    walk_keys = {
        (path.rsplit("/", 1)[-1], symbol)
        for path, symbol in (
            *set(_call_time_sites()),
            *set(_empty_pattern_sites()),
            *set(_census()),
        )
    }
    unresolved = sorted(
        key
        for key in (_basename_keys(CALL_TIME_REL) | _basename_keys(EMPTY_PATTERN))
        if key not in walk_keys
    )
    assert not unresolved, (
        "these register entries do not resolve into the site keyspace the walks "
        "report, so a reader declared there could never be matched against a "
        "derivation (and the 'already declared' branch of this arm could not "
        f"fire): {unresolved}"
    )

    # CONTROL: a synthetic member that IS declared must be reported. One derived
    # symbol from the first case is enough — the point is that the branch fires.
    control_file = _File(TORTISE / "<synthetic>", "<synthetic>", _NEW_READER_CASES[0][1])
    control_derived = sorted(
        set(_sites_in(control_file))
        | _empty_pattern_sites_in(control_file)
        | _call_time_sites_in(control_file)
    )
    assert control_derived, (
        f"{_NEW_READER_CASES[0][0]}: nothing derives, so the CONTROL below cannot "
        "be built and the 'already declared' branch stays unexercised"
    )
    control_symbol = control_derived[0][0]
    control = _new_reader_problems(declared | {("<synthetic>", control_symbol)})
    assert any(
        "already declared" in problem and control_symbol in problem for problem in control
    ), (
        "the control declared `(" + "<synthetic>, " + control_symbol + ")` and the "
        "predicate did not report it — the 'already declared' branch is dead code "
        "again, so half of this arm asserts nothing (the keyspaces of §13's rows "
        "and of the registers have drifted apart): got " + repr(control)
    )


#: FROZENSET cases for the named census's resolver: the rel family shapes that
#: used to fall through to `_UNKNOWN` because `_resolve` handled `ast.Tuple`/
#: `ast.List` only — so a `for rel in FAMILY` loop over a `frozenset` was missed
#: while a loop over a tuple was caught. Pinned here so the branch cannot regress.
_FROZENSET_CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "`ast.Set` literal",
        "RELS = {'x', 'aboutObject'}\n\n\n"
        "def read(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "`frozenset({...})` call",
        "RELS = frozenset({'x', 'aboutObject'})\n\n\n"
        "def read(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "`tuple([...])` call",
        "RELS = tuple(['x', 'aboutObject'])\n\n\n"
        "def read(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "`set({...})` call",
        "RELS = set({'x', 'aboutObject'})\n\n\n"
        "def read(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "`list([...])` call",
        "RELS = list(['x', 'aboutObject'])\n\n\n"
        "def read(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "a frozenset that does NOT carry the edge derives nothing",
        "RELS = frozenset({'x', 'y'})\n\n\n"
        "def read(g):\n    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        False,
    ),
)


def test_the_resolver_follows_a_frozenset_rel_family():
    """The resolver follows `ast.Set` and the collection constructors.

    `frozenset` is the IDIOMATIC shape for a rel family in this package
    (`DERIVABLE_STRUCTURAL_RELS`, `_ABOUT_REPLAY_RELS`, `ENTITY_LINKED_RELS` are
    all frozensets), and the resolver used to handle `ast.Tuple`/`ast.List` only —
    so a `for rel in FAMILY` loop over a tuple derived and one over a frozenset
    did not. The fourth review measured that asymmetry on a synthetic probe; these
    cases pin it, in both directions.
    """
    problems = []
    for label, source, should_derive in _FROZENSET_CASES:
        derived = _sites_in(_File(TORTISE / "<synthetic>", "<synthetic>", source))
        if bool(derived) is not should_derive:
            problems.append(
                f"{label}: expected {'a site' if should_derive else 'no site'}, "
                f"derived {sorted(derived)} — a `for rel in FAMILY` loop over "
                "this collection shape is not followed by `_resolve`"
            )
    assert not problems, (
        "the resolver cannot follow a rel-family collection shape, so a reader "
        "written the way this package actually writes them would derive nothing "
        "and ship undeclared:\n  - " + "\n  - ".join(problems)
    )



def test_the_census_derives_a_relationship_name_built_at_runtime():
    """The MECHANISM, isolated: every shape a rel name reaches a query in.

    The first revision of this guard read `ast.Constant` only, so five live
    readers that build the name at runtime were invisible to it while all four
    arms stayed green. These cases pin the derivation itself, on sources that do
    not depend on today's tree — a literal, a `+`-concatenation, an f-string,
    `"|".join(TUPLE)` and a `for rel in TUPLE` loop.
    """
    missing = []
    for label, source, should_derive in _MECHANISM_CASES:
        derived = _sites_in(_File(TORTISE / "<synthetic>", "<synthetic>", source))
        if bool(derived) is not should_derive:
            missing.append(
                f"{label}: expected {'a site' if should_derive else 'no site'}, "
                f"derived {sorted(derived)}"
            )
    assert not missing, (
        "the census lost a derivation shape, so a reader written that way would "
        "pass this guard undeclared:\n  - " + "\n  - ".join(missing)
    )


#: Synthetic sources for the empty-pattern arm: shapes that DO name no type, and
#: shapes that look similar but must not count (a named type is the named
#: census's business; a `MERGE` writes; a docstring only quotes Cypher).
_EMPTY_PATTERN_CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "outbound empty pattern",
        'def read(g):\n    g.query("MATCH (n)-[r]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "inbound empty pattern",
        'def read(g):\n    g.query("MATCH (n)<-[r]-(m) RETURN type(r)")\n',
        True,
    ),
    (
        "variable-length empty pattern",
        'def read(g):\n    g.query("MATCH (n)-[r*1..2]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "f-string with an empty pattern",
        'def read(g, label):\n'
        '    g.query(f"MATCH (n:{label})-[r]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "empty pattern carrying a property map (the colon is a map key)",
        'def read(g):\n'
        '    g.query("MATCH (n)-[r {idx:$i}]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "named type carrying a property map (not this class)",
        'def read(g):\n'
        '    g.query("MATCH (n)-[r:aboutObject {idx:$i}]->(m) RETURN n")\n',
        False,
    ),
    (
        "named type (the named census's job, not this class)",
        'def read(g):\n    g.query("MATCH (n)-[r:aboutObject]->(m) RETURN n")\n',
        False,
    ),
    (
        "bracket-less empty pattern (the other spelling of the same class)",
        'def read(g):\n    g.query("MATCH (n:Point)--() RETURN n.id")\n',
        True,
    ),
    (
        "bracket-less outbound traversal",
        'def read(g):\n    g.query("MATCH (n)-->(m) RETURN type(m)")\n',
        True,
    ),
    (
        "bracket-less inbound traversal",
        'def read(g):\n    g.query("MATCH (n)<--(m) RETURN n.id")\n',
        True,
    ),
    (
        "bracket-less both-directions traversal",
        'def read(g):\n    g.query("MATCH (n)<-->(m) RETURN n.id")\n',
        True,
    ),
    (
        "lowercase MATCH clause with an empty pattern",
        'def read(g):\n    g.query("match (n)-[r]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "a `--` inside a string literal is not a traversal",
        'def read(g):\n    g.query("MATCH (n) WHERE n.note = \'--\' RETURN n")\n',
        False,
    ),
    (
        "bracket-less MERGE writer",
        'def write(g):\n    g.query("MERGE (n)-->(m)")\n',
        False,
    ),
    (
        "MERGE writer",
        'def write(g):\n    g.query("MERGE (n)-[r]->(m)")\n',
        False,
    ),
    (
        "a docstring quoting the pattern is not a site",
        'def read(g):\n    """Tried `MATCH (n)<-[r]-(m)`; it scanned."""\n'
        "    return None\n",
        False,
    ),
)


def _empty_pattern_sites_in(file: _File) -> set[tuple[str, int]]:
    """`_empty_pattern_sites()` restricted to one (usually synthetic) module."""
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(_is_empty_pattern_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


def test_the_empty_pattern_class_derivation_holds():
    """The MECHANISM of the second class, on synthetic sources.

    The class is only as good as its shape detector: if the pattern match
    regressed, `_empty_pattern_sites()` would return nothing, the three §13 rows
    for its members would become "declared but not derived", and — worse — a new
    member would be silently acceptable. These cases pin both directions on
    sources that do not depend on today's tree, including the near-misses that
    must NOT count (a named type, a named type carrying a property map, a
    `MERGE` writer, and a docstring that only quotes Cypher) and the one shape a
    `":" in contents` test got wrong: `-[r {idx:$i}]->` names NO type, because
    that colon is a property-map key.
    """
    missing = []
    for label, source, should_derive in _EMPTY_PATTERN_CASES:
        derived = _empty_pattern_sites_in(_File(TORTISE / "<synthetic>", "<synthetic>", source))
        if bool(derived) is not should_derive:
            missing.append(
                f"{label}: expected {'a site' if should_derive else 'no site'}, "
                f"derived {sorted(derived)}"
            )
    assert not missing, (
        "the empty-pattern derivation lost a shape (or started counting one it "
        "must not), so a reader that names no type would pass undeclared:\n  - "
        + "\n  - ".join(missing)
    )


#: The sibling paragraph that narrates the census in `EXTRACTOR-V4-ARCHITECTURE.md`
#: (its opening line — the paragraph itself is one long line).
_EXTRACTOR_PASSAGE = "2. **Should `aboutObject` be a stored edge or a derived join?**"

#: A `file.py:NNNN` line hint. The census prose must carry NONE: a line number is
#: the one citation shape this guard cannot check (asserting it would red on every
#: unrelated edit), so the honest form is to omit it, as §13 already does.
_LINE_HINT = re.compile(r"\b[A-Za-z0-9_/]+\.py:\d[\d+-]*")

#: The sentence that declares the empty-pattern class *by its rows*.
_EMPTY_PATTERN_PROSE = re.compile(
    r"empty relationship pattern\*\*.{0,160}?\brows?\s+"
    r"((?:\d+)(?:[,\s]+(?:and\s+)?\d+)*)"
)

#: A backticked token in a `reach` cell that claims to be a code symbol.
_REACH_SYMBOL = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


@lru_cache(maxsize=1)
def _census_prose() -> tuple[tuple[str, int, str], ...]:
    """Every line that NARRATES the census: §13's bullet and the sibling paragraph.

    The census is a claim about the CODE and it is written twice — once as the
    table, once as prose around it. Only the table was parsed, so the prose could
    carry a stale line number, an under-counted class and a symbol that does not
    exist while every arm stayed green: a PASS about a false artifact, which is
    the failure mode this whole instrument exists to end.
    """
    lines: list[tuple[str, int, str]] = []
    inside = False
    for number, line in enumerate(STORAGE.read_text(encoding="utf-8").splitlines(), start=1):
        if not inside:
            inside = line.startswith(_BULLET_START)
            continue
        if line.startswith("- **") or line.startswith("---"):
            break
        lines.append(("STORAGE §13", number, line))
    for number, line in enumerate(
        EXTRACTOR.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.startswith(_EXTRACTOR_PASSAGE):
            lines.append(("EXTRACTOR-V4 (the aboutObject decision paragraph)", number, line))
    return tuple(lines)


def test_the_census_prose_carries_no_line_citations():
    """The census prose must state SYMBOLS, never `file.py:NNNN`.

    §13 states the rule for the whole census — *the symbols are authoritative and
    the line numbers are deliberately omitted*, because they rotted between
    `c79ba1cf2` and the current head — and then the sibling paragraph in
    `EXTRACTOR-V4-ARCHITECTURE.md` carried fifteen hints and said they were "the
    convenience, not the authority". At the head that rewrote it, six of the
    fifteen pointed at unrelated code (a comment inside another function, a
    `return` statement, a bare `*,` in a signature, a docstring line), and the
    same numbers were already different again one merge later. A re-verifier
    following one lands in unrelated code and concludes the passage is wrong.

    A line number is not something this guard can keep true — asserting it would
    red on every unrelated edit — so the claim is REMOVED rather than trusted.
    The symbols stay, and they are what the other arms resolve.
    """
    prose = _census_prose()
    assert prose, (
        "the census prose was not found at all, so this arm passes vacuously — "
        "`_census_prose()` located neither §13's bullet nor the sibling "
        "paragraph; re-point the markers at wherever the census is narrated"
    )
    assert any(where.startswith("EXTRACTOR") for where, _number, _line in prose), (
        "the sibling passage in `EXTRACTOR-V4-ARCHITECTURE.md` was not located, "
        "so half of this arm is checking nothing — the paragraph's opening line "
        "moved or was re-worded, and the fifteen line hints this arm exists for "
        "live in it: re-point `_EXTRACTOR_PASSAGE` at it"
    )
    offenders = [
        f"{where} line {number}: `{hint}`"
        for where, number, line in _census_prose()
        for hint in (m.group(0) for m in _LINE_HINT.finditer(line))
    ]
    assert not offenders, (
        "these `file.py:NNNN` hints are back in the census prose. Each one is a "
        "claim that rots on every edit to the file it cites — and the passage "
        "this arm was written for already had six of seven pointing at "
        "unrelated code — so state the symbol and drop the number (see §13's "
        "own sentence: the symbols are authoritative and the line numbers are "
        "deliberately omitted):\n  - " + "\n  - ".join(offenders)
    )


def test_the_prose_names_every_row_in_the_empty_pattern_class():
    r"""The class sentence is a prose RESTATEMENT of a derived set, so compare it.

    §13 declares the empty-pattern class by naming its rows: *"an empty
    relationship pattern (`MATCH (n)-[r]->(m)`, rows 3 and 17)"*. Row 18 (the
    backfill census, `OPTIONAL MATCH (o)<-[r]-()`) is the same class, and the
    guard's own docstring said so — so the sentence under-counted its own
    members. That is an under-counted declaration of the exact kind the table
    itself was four times corrected for, one level up, and it is what a
    re-verifier reads FIRST when told the class exists.

    The asserted set is derived (rows whose symbols are empty-pattern reads at
    this head), never a second hand list, so the sentence and the class cannot
    drift apart again.

    ⚠️ The derived set is restricted to the readers the shape walk is the ONLY
    route to — an empty-pattern reader whose about\*-relevance is gated by a
    relationship constant (`supersede_point`, `_preview_supersede`) is reached by
    the NAME census as well, so the sentence is not about it. That restriction is
    derived, not asserted by hand: the rows here are the empty-pattern rows the
    named census does not also derive.
    """
    named_keys = {(path.rsplit("/", 1)[-1], symbol) for path, symbol in _census()}
    derived_keys = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in _empty_pattern_sites()
    }
    call_time_keys = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in _call_time_sites()
    }
    shape_only = derived_keys - named_keys - call_time_keys
    derived_rows = {
        number
        for number, _site_cell, _reach, pairs in _declared_rows()
        if any(pair in shape_only for pair in pairs)
    }

    prose_rows: set[int] = set()
    found = False
    for _where, _number, line in _census_prose():
        for match in _EMPTY_PATTERN_PROSE.finditer(line):
            found = True
            prose_rows.update(int(token) for token in re.findall(r"\d+", match.group(1)))

    assert found, (
        "the sentence that declares the empty-pattern class by its rows is gone "
        "(or rewritten out of the shape this arm reads), so nothing says which "
        "rows belong to the class and a new member's row need not appear in it — "
        "re-word the sentence to name the rows after an `empty relationship "
        "pattern`, or delete this arm and move the declaration somewhere it can "
        "be checked"
    )
    assert prose_rows == derived_rows, (
        "the empty-pattern class's declared rows and the class derived from the "
        "code disagree. The sentence under-counted its own members once already "
        "(it said rows 3 and 17 while the backfill census, row 18 — "
        "`OPTIONAL MATCH (o)<-[r]-()` — is the same shape), which is the same "
        "direction as the table's four corrections and reads as authoritative to "
        "the next re-verifier. Rows only the shape walk reaches: "
        f"{sorted(derived_rows)}; rows the sentence names: {sorted(prose_rows)}"
    )


def test_the_reach_column_names_only_symbols_that_exist():
    """Every backticked symbol in a `reach` cell must exist in the package.

    The other arms resolve the `site` column, so `reach` was unguarded prose — and
    it cited `QueryOptions`, a name defined nowhere in `tortoise/` (the flag it
    describes is a keyword parameter of `tortoise_fts_query`). A non-existent
    symbol in the column that says WHO can reach a reader is the same defect as a
    non-existent row: a re-verifier looks for it, finds nothing, and distrusts
    the passage. This is deliberately a *presence* check, not a reachability
    claim — the prose around the symbol carries the reasoning.
    """
    corpus = "\n".join(_sources().values())

    checked = 0
    problems: list[str] = []
    for number, _site_cell, reach, _pairs in _declared_rows():
        for token in re.findall(r"`([^`]+)`", reach):
            token = token.strip()
            if not _REACH_SYMBOL.match(token):
                continue
            checked += 1
            if token not in corpus:
                problems.append(
                    f"row {number}: `{token}` is cited in `reach` and appears "
                    "nowhere in `tortoise/`"
                )

    assert checked >= 3, (
        f"only {checked} backticked symbols found in any `reach` cell — the "
        "column's shape changed and this arm would pass vacuously"
    )
    assert not problems, (
        "these `reach` cells cite symbols that do not exist in the package, so a "
        "re-verifier following the row finds nothing and distrusts the passage "
        "(correct the reference to the real symbol, or drop the backticks if it "
        "is prose):\n  - " + "\n  - ".join(problems)
    )


#: A backticked token in the sibling paragraph, reduced to its base symbol — so
#: `SubgraphExpander._neighbors` satisfies a row that names `_neighbors`, and
#: `tortoise_traverse` does NOT satisfy a row that names `traverse` (a different
#: symbol: the MCP tool versus the SDK method).
def _extractor_paragraph() -> str:
    """The sibling paragraph in `EXTRACTOR-V4-ARCHITECTURE.md`, verbatim."""
    return "\n".join(
        line for where, _number, line in _census_prose() if where.startswith("EXTRACTOR")
    )


def _extractor_symbols() -> set[str]:
    """The base symbols the sibling paragraph names in backticks."""
    return {
        _last(token.strip())
        for token in re.findall(r"`([^`]+)`", _extractor_paragraph())
        if _SYMBOL.match(token.strip())
    }


def test_the_sibling_paragraph_names_every_customer_callable_row():
    r"""The sibling SET is a restatement of §13's `customer-callable` rows.

    §13's count is the argument the KEEP ruling rests on, and the sibling
    paragraph states the SET behind it: *"the customer-callable surfaces are the
    ones §13's table marks `customer-callable`"*. At the head that added rows 21
    (`TortoiseSDK.traverse`) and 22 (`TortoiseSDK.ingest`) the paragraph was two
    rows short — it still enumerated the seven of the previous increment — while
    claiming to be that set. That is an under-counted declaration of exactly the
    kind the table itself was four times corrected for, and it is the paragraph a
    re-verifier reads to answer "did every customer-callable surface get named".

    The asserted set is the TABLE's, never a second hand list, so the paragraph
    and the table cannot drift apart again. Extra names in the paragraph are not a
    failure: it also narrates the default-ON and flag-gated readers, and that
    breadth is the point of keeping it — what it may not do is OMIT a
    customer-callable row.
    """
    paragraph = _extractor_paragraph()
    assert paragraph, (
        "the sibling paragraph was not located in `EXTRACTOR-V4-ARCHITECTURE.md` "
        "(the opening line moved?), so this arm would pass vacuously"
    )

    names = _extractor_symbols()
    problems: list[str] = []
    for number, _site_cell, reach, pairs in _declared_rows():
        if "customer-callable" not in reach.lower():
            continue
        if any(symbol in names for _file, symbol in pairs):
            continue
        problems.append(
            f"row {number} ({', '.join(sorted({s for _f, s in pairs}))}) is "
            "`customer-callable` in §13 and the sibling paragraph does not name it"
        )

    assert not problems, (
        "the sibling paragraph in `EXTRACTOR-V4-ARCHITECTURE.md` states that the "
        "customer-callable surfaces ARE §13's `customer-callable` rows, and it "
        "omits some of them — which is how the two customer doors this pass added "
        "stayed outside a sentence claiming to enumerate them (add the symbol, or "
        "drop the enumeration and leave the pointer):\n  - " + "\n  - ".join(problems)
    )


#: The sentence in §13 that enumerates every customer-callable surface, from the
#: colon that opens the list to the `are <N> of them` recital that closes it.
_CALLABLE_LIST_INTRO = "every customer-callable surface:"


def _split_entries(text: str) -> list[str]:
    """Split an enumeration on its top-level commas only.

    A parenthetical aside is part of the entry's IDENTITY — `(MCP
    `tortoise_recall`)` distinguishes it, and `(SDK, and hosted REST)` carries a
    comma that is not a separator — so the text is scanned with a paren-depth
    counter rather than de-parenthesised first. Stripping the asides made
    `search(order_by="graph")` and a hypothetical `search(order_by="degree")`
    collapse to the same `search`, i.e. a FALSE duplicate.
    """
    entries: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            entries.append("".join(current))
            current = []
            continue
        current.append(char)
    entries.append("".join(current))

    cleaned: list[str] = []
    for entry in entries:
        entry = entry.strip()
        if entry.lower().startswith("and "):
            entry = entry[4:].strip()
        if entry:
            cleaned.append(entry)
    return cleaned


def _callable_enumeration() -> tuple[tuple[str, ...], str | None]:
    """The customer-callable surfaces §13 ENUMERATES, and the numeral beside them.

    The `⇒` sentence is the one paragraph whose job is to be the reviewer's
    checklist for "did every customer-callable surface get named", and nothing
    read the list — `test_the_prose_counts_match_the_table` reads only the numeral
    *nine*, so the sentence could list the same surface twice and still pass (it
    did: the `tortoise_supersede` dry-run preview appeared twice, i.e. ten entries
    standing for nine surfaces).

    The numeral is returned with the entries because it is the count the LIST
    itself states — `... are nine of them` refers to the surfaces just enumerated,
    so it is the only like-for-like comparison available. Comparing the entries to
    the TABLE's row count would repeat the units error the headline arm exists for
    (a SYMBOL/LIST count against a ROW count): they agree today only because no row
    is split and no two rows share a surface.
    """
    text = _bullet_text()
    intro = text.find(_CALLABLE_LIST_INTRO)
    if intro < 0:
        return (), None
    rest = text[intro + len(_CALLABLE_LIST_INTRO) :]
    closing = _PROSE_CALLABLE_AGAIN.search(rest)
    if closing is None:
        return (), None
    return tuple(_split_entries(rest[: closing.start()])), closing.group(1)


def test_the_callable_enumeration_lists_each_surface_once():
    """The enumerated LIST is a claim too — each surface once, and all of them.

    A duplicated entry is not a cosmetic typo in this sentence: it reads as a
    surface that was checked twice, and it means one entry was not a surface at
    all. So the entries are checked twice — once for duplicates, once for their
    distinct count against the numeral THE SENTENCE states, which is what "are
    <N> of them" refers to. The numeral's own agreement with the table's
    customer-callable rows is arm 7's job, so a split row reds THERE (naming the
    numeral) instead of producing an unsatisfiable pair of assertions here.
    """
    entries, token = _callable_enumeration()
    assert entries, (
        "§13 no longer ENUMERATES its customer-callable surfaces (the "
        f"`{_CALLABLE_LIST_INTRO}` sentence is gone or re-worded), so the "
        "reviewer's checklist for that set is unguarded — restore the list, or "
        "delete this arm and say why in the docstring"
    )
    assert token is not None, (
        "the enumeration has no `are <N> of them` recital, so the surfaces it "
        "lists are not compared to any count — restore the numeral, or delete "
        "this arm"
    )
    stated = _numeral(token)
    distinct = set(entries)
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})

    problems: list[str] = [
        f"`{entry}` is listed {entries.count(entry)} times — one surface is one "
        "entry, and a duplicate means one listed surface was counted twice while "
        "another went unnamed"
        for entry in duplicates
    ]
    if stated is None:
        problems.append(
            f"the recital states `{token}` and this guard cannot read that numeral "
            "— spell it out in words or digits"
        )
    elif len(distinct) != stated:
        problems.append(
            f"the sentence enumerates {len(distinct)} distinct surfaces "
            f"({len(entries)} entries) and the recital beside it states {stated}"
        )

    assert not problems, (
        "the sentence §13 advertises as the checklist for its customer-callable "
        "SET disagrees with itself. The numeral is also checked against the table "
        "by `test_the_prose_counts_match_the_table`; this is the LIST, which "
        "nothing read:\n  - " + "\n  - ".join(problems)
    )


#: Readers whose Cypher is assembled ENTIRELY from variables — no bracket text for
#: any of the three shape walks to see, and no type slot for the name census. The
#: tree contains none today; they are here because the docstring states the limit
#: and a stated limit that is never measured is a claim.
_OPAQUE_READER_CASES: tuple[tuple[str, str], ...] = (
    (
        "the pattern fragment passed in and concatenated into the query",
        'def read(g, fragment):\n'
        '    g.query("MATCH (n)" + fragment + "(m) RETURN type(r)")\n',
    ),
    (
        "the whole query string built by a callee",
        'def read(g, build):\n    g.query(build())\n',
    ),
)


def test_a_pattern_built_entirely_from_a_variable_is_unreachable_and_says_so():
    r"""The census's stated LIMIT is real, and the docstring must say so honestly.

    The module docstring used to promise the escape hatch: a pattern assembled
    entirely from a variable *"must be declared by name — arm 6 checks that
    declaration"*. It cannot be. Arm 2 requires every §13 row to be a site of one
    of the three derivations, and arm 6's reverse direction requires every
    `CALL_TIME_REL`/`EMPTY_PATTERN` entry to still derive — so such a reader is
    **neither derived nor declarable**, which is the one failure mode this file
    exists to end. A reader who trusted the old sentence believed the census was
    total.

    Two halves, because only one of them is checkable in code:

    * the GAP is measured — these sources derive nothing, in any class;
    * the STATEMENT is pinned — the docstring must carry the honest form, so the
      promise cannot drift back without reding here. Closing the gap for real
      needs a new shape walk (how the string is assembled) or an explicit row /
      register exemption; neither exists today, and this arm is what makes that
      visible instead of assumed.
    """
    assert len(_OPAQUE_READER_CASES) >= 2, (
        "the stated limit is evidenced by fewer than two sources, so the gap "
        "below is measured on (or nothing at all) and this arm asserts only the "
        "docstring wording"
    )

    for label, source in _OPAQUE_READER_CASES:
        file = _File(TORTISE / "<synthetic>", "<synthetic>", source)
        derived = set(_sites_in(file)) | _empty_pattern_sites_in(file)
        derived |= _call_time_sites_in(file)
        assert not derived, (
            f"{label}: a derivation now sees this reader ({sorted(derived)}), so "
            "the stated limit is out of date — extend the docstring, or delete "
            "this case"
        )
        assert not _may_traverse_every_type(source) and not _may_have_gap_type(source), (
            f"{label}: the file FILTER would now admit this module, so the walks "
            "are reachable for a shape this arm says they cannot see"
        )

    doc = __doc__ or ""
    assert "neither derived nor declarable" in doc, (
        "the module docstring no longer states the limit honestly — it promised "
        "that a wholly-variable pattern 'must be declared by name — arm 6 checks "
        "that declaration', which both declaration surfaces REJECT (a §13 row reds "
        "arm 2; a register entry reds arm 6's reverse direction). Say it is "
        "neither derived nor declarable, and what closing it would need"
    )


@lru_cache(maxsize=1)
def _excluded_walk_sites() -> tuple[
    set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]
]:
    """The three derivations run over the modules `_files()` EXCLUDES.

    `_files()` is a text-predicate gate in front of the walks, so it must be a
    SUPERSET of what they would find: a gate one predicate too narrow un-sees a
    reader silently — the arm below would stay green while the census stopped
    covering it, which is this PR's own defect class one layer down.

    Only the EXCLUDED modules are walked, and that is the whole measurement: a
    gated module's sites are exactly what the cached walks already returned, so a
    narrow gate can only show up as a site in a module the gate skipped. Walking
    all 242 modules again measured ~19 s — it paid to re-derive every gated site
    for nothing, against the 4 s the filter exists to save.
    """
    gated = {file.path for file in _files()}
    named: set[tuple[str, str]] = set()
    empty: set[tuple[str, str]] = set()
    call_time: set[tuple[str, str]] = set()
    for path, source in _sources().items():
        if path in gated:
            continue
        rel = str(path.relative_to(ROOT))
        file = _File(path, rel, source)
        for symbol, _line in _sites_in(file):
            named.add((rel, symbol))
        if _may_traverse_every_type(source):
            for symbol, _line in _empty_pattern_sites_in(file):
                empty.add((rel, symbol))
        if _may_have_gap_type(source):
            for symbol, _line in _call_time_sites_in(file):
                call_time.add((rel, symbol))
    return named, empty, call_time


def test_the_file_filter_un_sees_nothing():
    r"""`_files()` must admit every module a walk would derive a site in.

    The PR body states that the four-way filter is a strict superset of the three
    walks — measured once by hand against a full 242-module walk — and NO test
    pinned it. A narrowed filter is the quietest failure this instrument can have:
    the walks still work, every arm still passes, and the reader they no longer
    reach simply stops being covered.

    So the three walks are run over the modules the filter SKIPPED (a gated
    module's sites are already the cached walks' output) and must find nothing in
    any class. The CONTROL matters as much as the check: the same measurement is
    run against a deliberately narrowed gate (`_literal_files()`, the single
    predicate the filter started from) and must DISAGREE — otherwise this arm
    could not see a narrowing at all and would be a PASS about nothing.
    """
    skipped_named, skipped_empty, skipped_call_time = _excluded_walk_sites()
    gated_named = {path for path, _symbol in _census()}
    gated_empty = {path for path, _symbol in _empty_pattern_sites()}
    gated_call_time = {path for path, _symbol in _call_time_sites()}

    problems: list[str] = []
    for label, gated, skipped in (
        ("named census", gated_named, skipped_named),
        ("empty-pattern class", gated_empty, skipped_empty),
        ("call-time class", gated_call_time, skipped_call_time),
    ):
        assert gated, (
            f"{label}: the gated walk derived nothing at all, so this arm would "
            "compare against an empty set"
        )
        missed = sorted(skipped)
        if missed:
            problems.append(
                f"{label}: {len(missed)} site(s) exist under `tortoise/` in a "
                f"module the file filter SKIPPED: {missed}"
            )

    assert len(_sources()) > len(_files()), (
        "the filter admits every module, so this arm has no excluded module to "
        "measure and passes vacuously — either the gate was widened to `True` or "
        "the package shrank to the gated set"
    )

    # CONTROL: the narrow gate (`_literal_files()` only, the single predicate the
    # filter started from) must MISS members of the three classes — a measurement
    # that cannot see a narrowing proves nothing about the widened gate.
    literal_only: set[tuple[str, str]] = set()
    for file in _literal_files():
        for symbol, _line in _sites_in(file):
            literal_only.add((file.rel, symbol))
        if _may_traverse_every_type(file.source):
            for symbol, _line in _empty_pattern_sites_in(file):
                literal_only.add((file.rel, symbol))
        if _may_have_gap_type(file.source):
            for symbol, _line in _call_time_sites_in(file):
                literal_only.add((file.rel, symbol))
    narrowed_away = (
        set(_census()) | set(_empty_pattern_sites()) | set(_call_time_sites())
    ) - literal_only
    assert narrowed_away, (
        "collapsing the filter to the literal carriers found every site the "
        "four-way filter found — so this arm cannot distinguish a narrowed gate "
        "from a correct one, and the superset property is unmeasured"
    )

    assert not problems, (
        "`_files()` no longer admits every module the walks would derive a site "
        "in, so a reader under `tortoise/` is silently outside the census — the "
        "failure this whole instrument exists to prevent, one layer down:\n  - "
        + "\n  - ".join(problems)
    )


#: Every synthetic corpus the mechanism arms iterate, by name. Each must hold both
#: directions, or its arm is a PASS about nothing. `_OPAQUE_READER_CASES` is
#: checked by its own arm below — its direction is the INVERSE (every case must
#: derive NOTHING), so it cannot join this loop's rule.
_CASE_CORPORA = (
    ("_MECHANISM_CASES", _MECHANISM_CASES),
    ("_CALL_TIME_CASES", _CALL_TIME_CASES),
    ("_FROZENSET_CASES", _FROZENSET_CASES),
    ("_EMPTY_PATTERN_CASES", _EMPTY_PATTERN_CASES),
)


def test_the_case_corpora_are_not_vacuous():
    """The synthetic CORPORA are the arms' evidence, so they are checked too.

    Two docstring claims about them were false at this head — `_EMPTY_PATTERN_CASES`
    was described as *seven* sources while it held nine, and `_MECHANISM_CASES` was
    credited with a `for rel in FROZENSET` loop that lives in `_FROZENSET_CASES` —
    in a file whose subject is that a count is a claim. Rather than restate the
    counts (a copy nobody reads is the defect), the properties the arms actually
    depend on are asserted: both directions present, no repeated label, a floor on
    the size. A corpus quietly shrunk to its deriving half leaves the arm above it
    green with nothing to prove — and a corpus emptied entirely leaves it green
    with nothing to ITERATE, which is why the size floor is the load-bearing check
    rather than a formality.

    `_OPAQUE_READER_CASES` is here rather than in `_CASE_CORPORA` because its rule
    is inverted: a limit's evidence is that NO case derives.
    """
    problems: list[str] = []
    for name, cases in _CASE_CORPORA:
        labels = [label for label, _source, _should in cases]
        if len(cases) < 4:
            problems.append(f"{name} holds {len(cases)} cases — too few to pin a shape")
        if not any(should for _l, _s, should in cases):
            problems.append(f"{name} holds no case that must derive")
        if not any(not should for _l, _s, should in cases):
            problems.append(
                f"{name} holds no near-miss that must NOT derive, so it cannot "
                "pin the detector's negative direction"
            )
        repeated = sorted({label for label in labels if labels.count(label) > 1})
        if repeated:
            problems.append(f"{name} repeats a case label: {repeated}")

    for name, cases in (
        ("_NEW_READER_CASES", _NEW_READER_CASES),
        ("_OPAQUE_READER_CASES", _OPAQUE_READER_CASES),
    ):
        if len(cases) < 2:
            problems.append(
                f"{name} holds {len(cases)} case(s) — the arm that leans on it "
                "has shrunk to measuring one shape, or to nothing at all"
            )
        repeated = sorted(
            {
                label
                for label, _source in cases
                if [cand for cand, _src in cases].count(label) > 1
            }
        )
        if repeated:
            problems.append(f"{name} repeats a case label: {repeated}")

    assert not problems, (
        "a synthetic corpus the mechanism arms depend on is no longer able to "
        "pin the shape it is cited for:\n  - " + "\n  - ".join(problems)
    )


#: The §16 intro sentence that states HOW MANY questions the list holds, and the
#: item form itself. The document promises three questions and enumerates them; a
#: rewrite that glues item 3 onto the tail of item 2's paragraph (`keep.**3. **…`)
#: leaves the promise false and nothing in this file could see it — the arms that
#: read this passage match its opening line and backticked symbols only.
_QUESTIONS_INTRO = re.compile(rf"the\s+({_NUMERAL})\s+questions below", re.IGNORECASE)
_QUESTION_ITEM = re.compile(r"(\d+)\.\s+\*\*")


def _question_list_problems(lines: list[str]) -> list[str]:
    """What is wrong with the numbered list the §16 intro sentence introduces.

    Separated from the document so the SAME predicate can be run on a synthetic
    sample — the control is what proves it can see an item that is not on its own
    line, rather than asserting that it can.
    """
    problems: list[str] = []
    start = next((i for i, line in enumerate(lines) if _QUESTIONS_INTRO.search(line)), None)
    if start is None:
        return [
            "the sentence that states how many questions follow it was not found, "
            "so this arm would measure an empty list"
        ]
    claimed = _numeral(_QUESTIONS_INTRO.search(lines[start]).group(1))  # type: ignore[union-attr]

    own_line: list[int] = []
    for offset, line in enumerate(lines[start + 1 :], start=start + 2):
        if line.startswith("---") or line.startswith("#"):
            break
        for match in _QUESTION_ITEM.finditer(line):
            number = int(match.group(1))
            if match.start() == 0:
                own_line.append(number)
            else:
                problems.append(
                    f"line {offset}: item {number} does not start a line — it is "
                    "glued to the tail of the paragraph above it, so the list "
                    "renders one item short and the glued question reads as prose"
                )

    if not own_line:
        problems.append(
            "no numbered item was found on its own line, so the list the sentence "
            "introduces is gone — the intro's marker moved, or the items did"
        )
        return problems
    if claimed is not None and len(own_line) != claimed:
        problems.append(
            f"the sentence says {claimed} question(s) and {len(own_line)} item(s) "
            f"start their own line: {own_line}"
        )
    if own_line != list(range(1, len(own_line) + 1)):
        problems.append(
            f"the items are not numbered contiguously from 1: {own_line} — a "
            "renumbering makes every cross-reference to *\"question N\"* point at "
            "a different question"
        )
    return problems


def test_the_extractor_section_keeps_its_three_questions():
    r"""§16's numbered list must still BE a list, item per line, as promised.

    The paragraph prefaces its list with *"The three questions below"* and §15
    cross-references *"question 3"*. This PR's own rewrite of item 2 removed the
    newline before item 3, so the question was appended to the end of the
    paragraph (`…cheap to keep.**3. **Should every expansion…`) and §16's list
    visibly held two items where the sentence above it claims three. Nothing saw
    it: `test_the_census_prose_carries_no_line_citations` scans that line for
    `file.py:NNNN`, and the sibling arm reads backticked SYMBOLS out of it — both
    survive a missing newline.

    A document that numbers what it promises is exactly this file's subject, one
    document over, so the promise is read: the count the sentence states, the
    items that start their own line, and their numbering must agree. The control
    runs the same predicate on a glued sample and on a clean one, so the arm
    measures a detector rather than trusting one.
    """
    lines = EXTRACTOR.read_text(encoding="utf-8").splitlines()
    problems = _question_list_problems(lines)

    clean = [
        "**⇒ The three questions below: 1 is OPEN.**",
        "",
        "1. **First?**",
        "2. **Second?**",
        "3. **Third?**",
        "",
        "---",
    ]
    glued = [
        "**⇒ The three questions below: 1 is OPEN.**",
        "",
        "2. **Second?** and then the tail:**3. **Third?**",
        "",
        "---",
    ]
    assert not _question_list_problems(clean), (
        "the control sample of three own-line items was reported as broken, so "
        "this arm's predicate cannot pass the shape it requires and the check "
        "below measures nothing: " + repr(_question_list_problems(clean))
    )
    control = _question_list_problems(glued)
    assert any("does not start a line" in problem for problem in control), (
        "the control sample with item 3 glued onto item 2's paragraph was NOT "
        "reported, so this arm cannot see the regression it was written for: got "
        + repr(control)
    )

    assert not problems, (
        "`EXTRACTOR-V4-ARCHITECTURE.md` §16 no longer renders the list its own "
        "sentence promises:\n  - " + "\n  - ".join(problems)
    )


#: A count stated in THIS file's prose about a set this file does not measure
#: there: the `EMPTY_PATTERN` register's size (which belongs to §13 and to
#: `test_the_empty_pattern_register_count_matches_the_prose`, the one reader of it),
#: a walk's member count (which the class arm asserts against the derived set), and
#: the `#7948` envelope's site count (which lives in that issue's owner-decision
#: comment and in `STORAGE-ARCHITECTURE-CHANGELOG.md` — neither of which this arm
#: can compare a restated numeral to, so a copy here is rot with a decoder ring).
#: All of them were restated here and all of them went stale — the register one in
#: three places while the arm went on asserting the document's numeral, and the
#: envelope one stated a figure that nothing claimed at all, which the ninth review
#: of PR #9398 found while this arm stayed green.
_SELF_COUNT_CLAIM = re.compile(
    rf"(?:{_NUMERAL})\s+members today"
    rf"|today's census:\s*(?:{_NUMERAL})\s+members"
    rf"|(?:{_NUMERAL})[- ]site envelope"
    rf"|(?:{_NUMERAL})\s+sites? claimed",
    re.IGNORECASE,
)


def test_the_file_states_no_count_it_does_not_measure():
    r"""This file may not restate a census numeral it does not itself assert.

    The register's size lives in three places at once: §13's sentence, the
    `EMPTY_PATTERN` tuple, and — before this arm — three comments here that quoted
    the sentence. The arm that polices the numeral reads the DOCUMENT
    (`test_the_empty_pattern_register_count_matches_the_prose`), so the register
    could grow from the numeral it used to state to the next one, red nothing, and
    leave three present-tense copies of the old number in the file whose subject is
    that a count is a claim. `_MIN_CALL_TIME`'s comment carried the same kind of
    copy for the call-time walk's member count, one numeral behind the derivation.

    The durable form the rest of this file already uses is to state the PROPERTY
    and point at the arm that measures it (`entry 14` in `WHAT THIS PINS` deleted
    its own count for exactly this reason). So the copies are banned rather than
    corrected: a count here is either asserted, or it is rot with a decoder ring.

    The fourth subject is the `#7948` envelope's site count, which nothing in this
    file measures: it is stated in that issue's owner-decision comment and recorded
    in `STORAGE-ARCHITECTURE-CHANGELOG.md`, and the docstring above restated it as a
    number neither of them claimed — in two spellings the original ban could not see
    (`<N>-site envelope`, `<N> sites claimed`). Both are matched now, so the copy
    cannot come back in a third shape.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    offenders = [
        f"line {number}: `{match.group(0)}`"
        for number, line in enumerate(source.splitlines(), start=1)
        for match in _SELF_COUNT_CLAIM.finditer(line)
    ]

    # CONTROL: the pattern must still match a restated count, or this arm is a
    # PASS about nothing. Assembled from parts so the sample cannot itself be the
    # match the arm reports.
    sample = "today's census: " + "seven" + " members"
    assert _SELF_COUNT_CLAIM.search(sample), (
        "the banned-count pattern no longer matches `today's census: <N> members`, "
        "so this arm would stay green over exactly the copies it exists to stop"
    )
    assert _SELF_COUNT_CLAIM.search("(" + "seven" + " members today"), (
        "the banned-count pattern no longer matches `(<N> members today`, so half "
        "of this arm asserts nothing"
    )
    # The two shapes this arm was WIDENED for: the docstring stated an envelope
    # count that nothing claimed, and `STORAGE-ARCHITECTURE-CHANGELOG.md` states
    # the same historical fact in the hyphaned form. Both samples are the pre-fix
    # text itself, built from parts, so "the ban can see that spelling" is a
    # measurement rather than a claim about the regex.
    assert _SELF_COUNT_CLAIM.search(
        "(`#7948`, " + "thirteen" + " sites claimed to be the envelope)"
    ), (
        "the banned-count pattern no longer matches `<N> sites claimed`, so the "
        "shape the ninth review found in this file's docstring can come back green"
    )
    assert _SELF_COUNT_CLAIM.search("the first " + "eleven" + "-site envelope"), (
        "the banned-count pattern no longer matches `<N>-site envelope`, so the "
        "changelog's spelling of the same claim is not covered by this ban"
    )
    assert not offenders, (
        "this file restates a census count it does not assert, so the copy will "
        "rot silently while the arm that owns the number stays green — state the "
        "property and point at the arm, or assert the numeral against the set it "
        "counts:\n  - " + "\n  - ".join(offenders)
    )


#: Arms the `WHAT THIS PINS` list collapses into another entry instead of giving a
#: number of their own. Declared rather than assumed, so "every arm is numbered"
#: stays a MEASUREMENT: the arm below requires every `test_` function to be either
#: a numbered entry or a name in this set.
_COLLAPSED_ARMS = frozenset(
    {
        # Entry 5 owns it: the rel-family constructors are that resolver's
        # branches, and the docstring says so in the entry itself.
        "test_the_resolver_follows_a_frozenset_rel_family",
    }
)

#: A numbered entry of the `WHAT THIS PINS` list: `<n>. **`test_...`**`.
_NUMBERED_ENTRY = re.compile(r"^(\d+)\.\s+\*\*`(test_[A-Za-z0-9_]+)`\*\*", re.MULTILINE)


def test_the_docstring_names_every_arm():
    """The `WHAT THIS PINS` list IS the numbering of record, so it is read.

    The docstring states that its numbered list *"is the numbering of record"* —
    and two arms had no entry in it (`test_the_call_time_shape_derivation_holds`
    and `test_a_new_call_time_reader_cannot_ship_green`, the mechanism and
    new-member arms of the third class) while three separate places in the file
    refer to arms by number. A reader who audits *"does every arm have a stated
    purpose"* got a list that silently omitted the class a review was convened
    about, which is the same failure direction as every count this file polices:
    the claim was in prose and nothing compared it to the set it describes.

    So the comparison is executable: every `test_` function in this module must be
    either a numbered entry or a declared collapse, the numbers must be contiguous
    with no duplicates (a renumbering that leaves two entry 12s would let every
    *"arm N"* reference in the prose resolve to the wrong arm), and every collapsed
    name must be a real arm that the docstring still names.
    """
    document = globals().get("__doc__") or ""
    assert len(document) > 2000, (
        "the module docstring is gone or truncated, so this arm would compare the "
        "arms to nothing — restore it or delete this arm and say why"
    )

    entries = _NUMBERED_ENTRY.findall(document)
    numbered = [name for _number, name in entries]
    numbers = [int(number) for number, _name in entries]

    problems: list[str] = []
    if not numbers:
        problems.append(
            "the `WHAT THIS PINS` list parsed to zero numbered entries — the "
            "format this arm reads has changed, not the list's content"
        )
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    if duplicates:
        problems.append(f"duplicate entry number(s) in the list: {duplicates}")
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        problems.append(
            f"the entry numbers are not contiguous from 1: {numbers} — an *\"arm N\"* "
            "reference elsewhere in this file would then resolve to the wrong arm"
        )

    arms = sorted(
        name
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    )
    if len(arms) < 20:
        problems.append(
            f"only {len(arms)} arms were collected from this module — the harness "
            "is reading the wrong namespace, so 'every arm is numbered' would be "
            "vacuously true"
        )
    unnamed = [name for name in arms if name not in numbered and name not in _COLLAPSED_ARMS]
    if unnamed:
        problems.append(
            "these arms have no numbered entry in `WHAT THIS PINS` and are not a "
            f"declared collapse: {unnamed} — add an entry (the list is the numbering "
            "of record) or declare the collapse in `_COLLAPSED_ARMS` with the entry "
            "that owns it"
        )
    stale_collapsed = sorted(name for name in _COLLAPSED_ARMS if name not in arms)
    if stale_collapsed:
        problems.append(
            f"`_COLLAPSED_ARMS` names arms this module no longer has: {stale_collapsed}"
        )
    uncited_collapsed = sorted(name for name in _COLLAPSED_ARMS if name not in document)
    if uncited_collapsed:
        problems.append(
            "these arms are declared as collapsed but the docstring never names "
            f"them, so the collapse is invisible to a reader: {uncited_collapsed}"
        )
    phantom = sorted(name for name in numbered if name not in arms)
    if phantom:
        problems.append(
            f"`WHAT THIS PINS` numbers arms this module does not define: {phantom}"
        )

    assert not problems, (
        "the docstring's numbering of record no longer describes this file:\n  - "
        + "\n  - ".join(problems)
    )
