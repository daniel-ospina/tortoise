"""#13094 — the change feed's readers, and the two things that block its move.

WHAT THIS FILE PINS, and what each pin would be FALSE without:

1. **The reader census is an ARTIFACT, not prose.** `tools/graphevent_home.READERS`
   is the per-reader table `#13094` ships; every entry's statements are asserted
   to still appear in the file it names, and where one file holds the same
   statement twice (`min_occurrences`) BOTH occurrences are required — a shared
   snippet must not let one row vouch for two. Without that, the table is an
   evidence document that drifts one merge behind the code it claims to describe
   — the class `#13113` filed an extractor for.

2. **No reader needs a graph capability.** The census's verdict is
   ``record_only``-shaped: every reader is a key lookup, an ordered range scan, an
   aggregate, a predicate delete, or the schema those three depend on. The pin is
   a VOCABULARY test over the table, so a future reader that genuinely needs a
   traversal cannot be added as "maintenance" without going red.

3. **`journal_event` is not the change feed's home, and the reason is a producer
   contract — not a missing column.** Two independent, EXECUTED refutations:

   * a payload-only emit writes a `:GraphEvent` row and **no journal row at all**
     (`_emit_event` returns before the JSONL half when it has no ``point``/``id``/
     ``turns``), so the record's stream is short by construction for the
     hard-delete lane — the shape `#13083` §6.3 named and this test EXECUTES,
     with a POSITIVE CONTROL beside it (an emit that carries a point DOES journal)
     so the empty half cannot pass for a fixture that never bound a journal;
   * the two stores' `seq`s are different coordinates (`:GraphEvent.seq` is the
     change feed's own allocator, `journal_event.seq` is the replay journal's
     ordinal, `#12894`), so no join on `seq` can be a cursor.

4. **The tier mechanism cannot express this move — and `#4664` is why.**
   `cache_projection.read_record_view` builds its SELECT from `NODE_TABLES` for
   EVERY label it is asked for, so "give it a `NODE_TABLES` row, then tier it"
   would make a record-sourced rebuild try to PROJECT `:GraphEvent` back into the
   graph: the exact resurrection `#4664` says must not happen. The test patches
   the two registries and shows the reader reaching for the table, then refusing
   on an UNCLASSIFIED column (`UnknownRecordColumn` — the placement table is the
   lock) — and the key contract (`node_identity`'s CHECK has no `GraphEvent`, and
   `seq` is not a string key) is the third, NAMED but not executed: reaching it
   needs the column classification this test shows is missing.

5. **A read that could not run produces no verdict.** Exit 2, never "covered" —
   for a missing graph URI, a missing record DSN, `psycopg2` absent, a graph the
   server does not hold (a read against an absent graph CREATES it), an EMPTY
   feed (an empty multiset is a subset of everything: the vacuous PASS), and a
   store that refused.

6. **The instrument is selected by its own diff.** `tools/` is dropped by
   `_keep_changed`, so a tool-only change selects nothing unless the tool is in
   `CORE_ALSO` — the silent-drop class `tools/published_artifact_canary.py` and
   `tools/measure_4211_session_hierarchy.py` each carry a row and a pin for. That
   entry's pin lives where the repo keeps its entry-rule pins,
   `tests/test_ci_selection.py::test_a_graphevent_census_tool_only_diff_selects_its_pins`,
   not here.

Nothing here changes product behaviour. `#13094`'s move is a STORE re-home (the
change feed is not a projection of the record), recorded as the issue's remaining
scope (its comment 7065026251) — not a peer ticket, because the root is the issue.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "graphevent_home.py"
MEASURE = (REPO_ROOT / "docs" / "evidence" / "13094-graphevent"
           / "measure_graphevent_residency.py")

#: Capability words a reader would have to need for the shape to stay resident.
#: The vocabulary is the audit's own (`#13083` §2): the four capabilities only a
#: graph engine provides, plus EP state. The PREDICATE lives in the tool, not
#: here — a check whose vocabulary the checked module does not own is a second
#: definition that drifts. This tuple is the EXPECTED set, pinned against the
#: tool's own in `test_the_capability_vocabulary_has_one_owner`, so a narrowing
#: edit to the tool cannot silently loosen the census pin.
GRAPH_CAPABILITIES = ("traversal", "adjacency", "fulltext", "vector", "ep walk",
                      "variable-length")

#: The controls for the CYPHER arm, as a SPECIFICATION: `(label, snippet, the arm
#: that MUST catch it)`. ⛔ The expected arm is a LITERAL, never recomputed from
#: `GRAPH_CAPABILITY_CYPHER` — an expectation derived from the constant under
#: test moves with the mutant, so deleting a token (or re-ordering the tuple)
#: leaves every row green. That is the `agent-infra#3442` defect exactly: the
#: oracle must not be derived from the system under test (`#14183` review,
#: findings 1 and 4). With literals, a deleted token reds the rows that name it.
CAPABILITY_CONTROLS = (
    # variable-length — the only shape a `-[*`-only detector could see
    ("variable-length", "MATCH (e:GraphEvent)-[*1..2]->(n) RETURN n",
     "cypher:-[*"),
    # FIXED-length adjacency — the audit's own definition of the capability, and
    # the one shape a `-[*`-only detector cannot see
    ("fixed-length adjacency",
     "MATCH (e:GraphEvent)-[r:TOUCHED]->(p:Point) RETURN p", "cypher:-["),
    # ⛔ THE SAME SHAPE, RE-SPACED — the pin for `_NO_WS`. The engine ACCEPTS
    # `- [r:T] ->`, so while both sides were matched as contiguous substrings this
    # returned `None`: a one-hop reader spaced like this was invisible to the
    # guard (`#14183` review, non-blocking 6, measured). Strip the whitespace from
    # either side of the tool's match and THIS row is the one that reds — the raw
    # form carries neither `-[` nor `]->` nor `--`.
    ("fixed-length adjacency, RE-SPACED",
     "MATCH (e:GraphEvent) - [r:TOUCHED] -> (p:Point) RETURN p", "cypher:-["),
    # The UNDIRECTED re-spaced spelling (round-4 review): its raw form likewise
    # carries no arm at all, so it reds on the same mutant from the other side.
    ("re-spaced undirected adjacency",
     "MATCH (a) - [r:IMPL|NAND]- (b)", "cypher:-["),
    # ⛔ THE SAME SHAPE WITH A COMMENT INSIDE THE HOP — the pin for
    # `_LITERAL_OR_COMMENT`. Cypher accepts a comment between the `-` and the `[`,
    # so this spelling carries no contiguous arm: measured before the fix,
    # `graph_capability` returned `None` for BOTH rows below while the re-spaced
    # rows above were caught (round-12 review, P2 — the same false-PASS direction
    # as the backtick and the space, one spelling further out). Strip comments
    # before matching and these two rows are what reds.
    ("adjacency with a BLOCK comment inside the hop",
     "MATCH (e:GraphEvent)-/* hop */[r:TOUCHED]-(p:Point) RETURN p", "cypher:-["),
    ("adjacency with a LINE comment inside the hop",
     "MATCH (e:GraphEvent)-//hop\n[r:TOUCHED]-(p:Point) RETURN p", "cypher:-["),
    # ⛔ A `//` INSIDE A BACKTICK-QUOTED IDENTIFIER, the one spelling the write
    # gate's comment grammar alone cannot survive: it has no backtick branch, so
    # its `//` branch fires INSIDE `` `a//b` `` and eats the rest of the statement —
    # the pattern included. Measured: `None` before the guard's backtick branch,
    # `cypher:-[` after. `test_the_noise_stripper_is_the_write_gates_grammar_plus_
    # one_backtick_branch` pins the same delta from the grammar side.
    ("pattern after a backtick identifier containing `//`",
     "MATCH (n) WHERE n.`a//b` = 1 MATCH (e)-[r:T]-(p)", "cypher:-["),
    # The DIRECTED re-spaced spelling pins the SECOND half: raw it is caught by
    # `]->` alone, and on the stripped form by the earlier `-[` arm — so the
    # literal expectation here fixes arm PRECEDENCE, not just detection.
    ("re-spaced directed adjacency",
     "MATCH (e:GraphEvent) - [r:T]-> (p)", "cypher:-["),
    # the live EP / belief walk CAPABILITY-PLACEMENT-AUDIT.md quotes in its §2.1 `:Point` table's
    # EP-walk row (sourced from `ep.py:1620-1632`), with its `{label}` placeholder FILLED BY
    # `GraphEvent` — a label the walk is never swept over (`live.py:667`'s `EPISTEMIC_LABELS` is
    # Point/Subject/Object/Event), so this row pins the SHAPE the capability arms must catch, not a
    # placement claim. ⛔ Named by SECTION and row, never as a `:NNNN` address into that document:
    # the audit is a file this very PR grows, so a bare line number here went stale by this diff's
    # own insertions (the EP-walk row is `:410` on `main` and `:431` here — re-measured by
    # `grep -n 'ep.py:1620-1632'`, and the delta is this PR's own header rows; #14183 review
    # P3) and a lane re-deriving from it lands on a blank line. The document's own §2.1 anchors it.
    ("EP walk",
     "MATCH (a:GraphEvent) WHERE a.id IN $ids WITH * MATCH (a)-[r:IMPL|NAND]-(b)",
     "cypher:-["),
    # a typed variable-length pattern (no `-[*` literal)
    ("typed var-length", "MATCH (e:GraphEvent)-[r:NEXT*]->(n)", "cypher:*]"),
    # edge-existence predicate, node-existence predicate, adjacency aggregate,
    # and the audit's own undirected hop (`NOT (n)--()`)
    ("edge existence",
     "MATCH (e:GraphEvent) WHERE EXISTS { (e)-[:TOUCHED]->(:Point) } RETURN e",
     "cypher:-["),
    ("node existence", "MATCH (n:Point) WHERE EXISTS { (n:Point) } RETURN n",
     "cypher:exists {"),
    ("adjacency aggregate", "MATCH (e:GraphEvent) RETURN size((e)-->())",
     "cypher:--"),
    ("undirected hop", "MATCH (n:Point) WHERE NOT (n)--() RETURN n", "cypher:--"),
    # procedures and algorithms. `apoc.` is the shape the census's own
    # before/after table advertises, so it is the one that must not ship untested.
    ("path procedure",
     "MATCH (e:GraphEvent) CALL apoc.path.expand(e, 'TOUCHED>', 'Point', 1, 2)",
     "cypher:apoc."),
    # ⛔ THE HOP-COUNT ARMS DO DECIDE, on spellings a real reader carries
    # (`#14183` round-7 review, P2). The earlier cut declared `*1..`, `*..` and
    # `shortestpath` undecidable on the grounds that "every real spelling also
    # carries an earlier arm" — measured FALSE for a TYPED bounded hop (it carries
    # no `-[*`), an open-ended hop, and the shortestPath PROCEDURE (a `CALL`, so
    # nothing precedes it). Three arms were shipping with their identity graded by
    # nothing, which is the defect this table exists to end.
    ("typed bounded var-length", "MATCH (a:GraphEvent)-[:IMPL*1..5]->(b)",
     "cypher:*1.."),
    # ⛔ and the BARE undirected hop. `<-` was declared undecidable on the grounds
    # that it is "always written `<-[:T]-`, which carries `-[`" — measured FALSE for
    # `MATCH (a)<--(b)`, the untyped form, which carries no `-[` and is the one `<-`
    # decides (`#14183` round-6 finding 1, the sibling of the three rows above).
    ("bare undirected hop", "MATCH (a)<--(b)", "cypher:<-"),
    ("open-ended var-length", "MATCH p=(a)-[:NEXT*..3]->(b)", "cypher:*.."),
    ("shortest path procedure",
     "CALL algo.shortestPath.stream(null, null, {relationshipQuery: 'IMPL'})",
     "cypher:shortestpath"),
    # ⛔ BACKTICK-QUOTED PROCEDURE NAMES (`#14183` round-8 review, P2/P3). The engine
    # accepts ``CALL `algo.pageRank`(…)``, and this repo's own write gate documents
    # that spelling as valid Cypher (`tortoise/projection/__init__.py:3572` carries
    # the explicit `` `? `` in `_CALL_PROC_RE`; `:3594` states the validity). While
    # the match was whitespace-only a backticked graph
    # algorithm returned `None` — a capability reader past every check, the
    # FALSE-PASS direction this guard exists to close. These two rows are the pin
    # for the backtick half of `_NO_WS`: the first proves `call algo` is not a miss,
    # the second proves the quoted fulltext call lands on the SAME early arm as its
    # unquoted spelling (so the bare `db.idx.*` arms below stay undecidable).
    ("backticked graph algorithm",
     "CALL `algo.pageRank`(null, null) YIELD nodeId, score", "cypher:call algo"),
    ("backticked fulltext procedure",
     "CALL `db.idx.fulltext.queryNodes`('Point', $q) YIELD node",
     "cypher:call db.idx.fulltext"),
    ("graph algorithm", "CALL algo.pageRank(null, null) YIELD nodeId, score",
     "cypher:call algo"),
    ("fulltext procedure",
     "CALL db.idx.fulltext.queryNodes('Point', $q) YIELD node",
     "cypher:call db.idx.fulltext"),
    ("vector procedure",
     "CALL db.idx.vector.queryNodes('Point', 'emb', 10, $v) YIELD node",
     "cypher:call db.idx.vector"),
)

#: Arms that can never be the DECIDING token, because every real spelling of them
#: also carries an EARLIER arm in `GRAPH_CAPABILITY_CYPHER` and the tool takes the
#: first match: `-[r]->` also carries `-[`, `<-[:T]-` also carries `-[`, and a
#: `size((…))` adjacency aggregate needs a relationship inside it, so `-[` or `--`
#: decides first. They are real arms — a snippet carrying only that substring is
#: still refused — they simply cannot be pinned by a snippet a real reader would
#: carry. Declared here so the completeness pin can tell "not pinned yet" (a
#: defect) from "cannot be pinned" (a property of first-match ordering).
#:
#: ⛔ THIS SET IS A CLAIM ABOUT ORDERING, SO IT IS SMALL ON PURPOSE, and every
#: member was checked by construction rather than by intuition: `*1..`, `*..` and
#: `shortestpath` were ON this list until a review measured that a typed bounded
#: hop, an open-ended hop and the shortestPath procedure each decide their own arm
#: (`#14183` round-7, P2). A token that CAN be pinned belongs in
#: `CAPABILITY_CONTROLS`; the disjointness assert below makes a row that decides a
#: declared arm red instead of silently leaving the declaration stale.
ARMS_NO_SNIPPET_CAN_DECIDE = {
    "]->",           # always preceded by `-[`
    # ⛔ `<-` was here and DID NOT BELONG: `<--` carries no `-[`, so `<-` decides it.
    # It is pinned above. What is left here must be undecidable by FIRST-MATCH
    # ORDERING — the completeness pin asserts this set and the control rows are a
    # PARTITION, so a token that a row decides cannot stay declared.
    "size((",        # a pattern aggregate needs a relationship → `-[` or `--`
    # Every realistic spelling of these is a `CALL`: the operator spells the
    # procedure with its `call ` prefix (backticked or not — the strip makes
    # `` `db.idx… `` the same text as `db.idx…`), and the `call db.idx.*` arm is
    # ordered before the bare name. ⚠️ The round-9 note that used to sit here —
    # that an engine-accepted COMMENT between the two (`CALL /*idx*/
    # db.idx.fulltext.queryNodes(…)`) left the BARE arm to decide and was therefore
    # "not a hole" — is RETRACTED at round 12, because that reasoning did not hold
    # where it mattered: for the ADJACENCY arms a comment inside the hop defeated
    # EVERY arm and the row was misfiled, not refused (measured `None`). Comments
    # are stripped before matching now (see the tool's `_NO_WS` block), so the
    # `call db.idx.*` arm decides that spelling too. The two bare arms stay
    # declared because their `call ` prefix is ordered before them, which is what
    # their own control rows pin.
    "db.idx.fulltext",
    "db.idx.vector",
}


def _tool():
    """The verb, imported by path (`tools/` is not a package)."""
    spec = importlib.util.spec_from_file_location("graphevent_home", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _measure():
    """The measurement script, imported by path (`docs/` is not a package).

    Its `_connection` imports `falkordb` INSIDE the function, so importing the
    module needs no driver and no server — which is what lets the refusal path be
    pinned without one.
    """
    spec = importlib.util.spec_from_file_location("measure_graphevent_residency",
                                                  MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ── 1. the census is checkable ─────────────────────────────────────────────

def test_every_declared_reader_still_names_its_statement_in_the_tree():
    """Every row of the shipped reader table must still resolve in its file.

    CONTENT-matched, not line-matched, and deliberately so: these statements are
    written as concatenated string literals and sit inside functions that other
    PRs edit above them, so a line-number pin would red on every unrelated merge
    and be "fixed" by renumbering — which is how a citation stops meaning
    anything. The declared `line` is an address at the head it was cut at; the
    ASSERTION is on the statement text.

    ⛔ `min_occurrences` + `occurrences` are what make the pin able to catch a
    LOSS. Two rows in `event_store.py` share one statement text (the dedupe probe
    and `_event_row_exists`), as do the two restore-side scans in
    `hosted_backup.py`: a single-occurrence check would let one survivor satisfy
    both rows, so deleting the other would go unnoticed. ⛔ And a FLOOR is not
    enough — measured on `hosted_backup.py`, the statement occurs FOUR times
    (`:1333`, `:1534`, the live read at `:3603` and a docstring mention at
    `:3442`), so deleting BOTH censused sites still leaves 2 and a
    `min_occurrences: 2` floor stays GREEN with both rows pointing at deleted
    code. `occurrences` therefore pins the EXACT count where a row declares it,
    which is what makes any deletion of these sites red.
    """
    module = _tool()
    missing = []
    for reader in module.READERS:
        source = _collapse((REPO_ROOT / reader["file"]).read_text(encoding="utf-8"))
        for snippet in reader["snippets"]:
            needed = reader.get("min_occurrences", 1)
            found = source.count(_collapse(snippet))
            if found < needed:
                missing.append(f"{reader['file']}:{reader['line']} "
                               f"{reader['reader']} — {snippet!r} "
                               f"(found {found}, needs {needed})")
            exact = reader.get("occurrences")
            if exact is not None and found != exact:
                missing.append(f"{reader['file']}:{reader['line']} "
                               f"{reader['reader']} — {snippet!r} "
                               f"(found {found}, pinned EXACTLY {exact}: an "
                               "occurrence was deleted or added — re-derive the "
                               "row at the CURRENT head)")
    assert not missing, (
        "the reader census no longer resolves — re-derive the row(s) (and the "
        "`line` addresses) at the CURRENT head, never by renumbering:\n  "
        + "\n  ".join(missing))


def test_the_census_covers_the_labels_schema_as_well_as_its_readers():
    """The three `ensure_event_schema` DDL statements are censused, not assumed.

    They do not READ a row — which is why an earlier cut of this census left them
    out — but they are the schema a re-home must recreate (`read_after`'s ordered
    scan plans against the `seq` index; the `event_id` index backs the dedupe
    probe), so leaving them out is how a move ships a store with no indexes.
    """
    module = _tool()
    schema = [r for r in module.READERS if "DDL" in r["needs"]]
    assert len(schema) == 3, [r["reader"] for r in schema]
    for reader in schema:
        assert reader["file"] == "tortoise/event_store.py"
        assert reader["class"] == "maintenance"


def test_the_unique_constraint_row_names_the_layer_that_really_holds():
    """Which dedupe is IN FORCE — and the store's own bytes must agree with the row.

    ⛔ #14183 review round 10 (P3): the row opened “the belt behind the dedupe
    pre-check (the app-side probe is the suspenders)” and, four clauses later, said
    “the app-side probe is the belt that is actually there”. Both orders in ONE
    string: a reader could take either, and only the second is measured (the store
    admits a duplicated `(seq, event_id)` row with no constraint — this directory's
    own `measurement-payload88.json` records the refusal).

    ⛔ THE ARM IS THE SOURCE, not a wording count. The claim the row makes is that
    `ensure_event_schema` sends the constraint through `g.query()`, which cannot
    parse a module command, so the constraint never lands and the APP-SIDE pre-check
    is the belt. That is checkable in the store's own text, so a lane that fixes
    `#14212` by issuing it the module way reds HERE and must re-derive the row: the
    census describes the store's shape, and must move when the shape moves. A drift
    control — never a reason to leave the defect standing.
    """
    module = _tool()
    row = next(r for r in module.READERS
               if r["reader"] == "ensure_event_schema (unique constraint)")

    source = (REPO_ROOT / "tortoise" / "event_store.py").read_text()
    start = source.index("def ensure_event_schema(")
    body = source[start:source.index("\ndef ", start + 1)]
    assert "GRAPH.CONSTRAINT CREATE" in body, (
        "the census row's snippet is no longer issued by ensure_event_schema — "
        "re-derive the row rather than keeping a snippet nothing sends")
    assert "g.query(" in body and "execute_command(" not in body, (
        "ensure_event_schema now issues the constraint the MODULE way, so it CAN "
        "land in production: the row's claim that it never does is stale — "
        "re-derive the row and close #14212 instead of keeping the comment")

    assert "the app-side dedupe pre-check" in row["why"] and "is the belt" in row["why"], (
        f"the row must name the layer that actually holds: {row['why']!r}")
    assert "the belt behind the dedupe pre-check" not in row["why"], (
        "the row calls the CONSTRAINT “the belt behind the dedupe pre-check” while "
        "also saying the probe is “the belt that is actually there” — the "
        "self-contradiction #14183 round 10 measured; state one order, the measured one")


def test_the_capability_vocabulary_has_one_owner():
    """The census pin's vocabulary must be the TOOL's, not a second copy.

    A test that keeps its own private word list can drift from the predicate it
    claims to exercise: remove a word from the tool and the pin here would keep
    passing over a capability the tool no longer recognises. Pinned as a set so
    the ordering is not load-bearing.
    """
    module = _tool()
    assert set(module.GRAPH_CAPABILITY_WORDS) == set(GRAPH_CAPABILITIES)
    assert module.GRAPH_CAPABILITY_CYPHER, (
        "the Cypher arm of the capability predicate is empty — the prose arm "
        "alone is the vacuous check this pin exists to prevent")


def test_no_reader_of_the_change_feed_needs_a_graph_capability():
    """The verdict the whole issue rests on, as a check on the table itself.

    ⛔ The pin is not "the table says so" — it is that NO row needs a graph
    capability. A future reader that needs one cannot be filed as `maintenance`
    without reddening this, which is what makes `record_only`-shaped a property
    of the census rather than a claim beside it.

    ⛔ THE PREDICATE IS THE TOOL'S, and it reads the CYPHER as well as the prose.
    An earlier cut of this pin scanned `needs`/`why` only, which is the one place
    a capability does NOT live: a row carrying
    `MATCH (e:GraphEvent)-[*1..2]->(n) RETURN n` with prose worded around the
    vocabulary filed as `maintenance` left every pin green while the evidence doc
    promised the opposite (#14164 review, finding 3). The `snippets` arm below is
    the case that made that true, kept as an EXECUTED control rather than as a
    comment claiming it.
    """
    module = _tool()
    offenders = [
        f"{r['file']}:{r['line']} {r['reader']} — needs {capability}"
        for r in module.READERS
        if (capability := module.graph_capability(r))
    ]
    assert not offenders, (
        "a reader of the change feed now needs a graph capability, so the "
        "shape is NOT a record_only candidate — re-open #13094's verdict:\n  "
        + "\n  ".join(offenders))

    # The controls: the predicate must CATCH a capability that lives only in the
    # snippet, under a class that claims none — and the arm asserted is the
    # LITERAL from `CAPABILITY_CONTROLS`, not one recomputed from the tool's own
    # tuple. A recomputed expectation is `X == X` whenever any arm matches (the
    # same constant, the same iteration order), so it cannot see a deleted token
    # (`#14183` review, findings 1 and 4).
    for label, cypher, expected_arm in CAPABILITY_CONTROLS:
        smuggled = {
            "file": "tortoise/event_store.py", "line": 992, "reader": "walk_out",
            "needs": "walks outward from each event",
            "why": "feed enrichment", "class": "maintenance",
            "snippets": (cypher,),
        }
        caught = module.graph_capability(smuggled)
        assert caught == expected_arm, (
            f"the {label} case must be caught by {expected_arm!r} "
            f"(got {caught!r}) — a capability reader filed as `maintenance` "
            "passes this pin, and the census can go false with every pin green")


def test_every_capability_arm_is_pinned_or_declared_unreachable():
    """No arm of the guard may be unpinnable by ACCIDENT.

    A token in `GRAPH_CAPABILITY_CYPHER` with no control row is a claim about the
    code that nothing grades: `apoc.` and `call algo` shipped with no fixture at
    all while the census's own table advertised the `apoc.` shape as covered, and
    the six rows that did exist could only falsify ONE of the nine added tokens
    (`#14183` review, round 5 finding 4 — a recurrence of round 4's finding).

    So this pin forces the choice: every arm is either pinned by a literal
    expectation in `CAPABILITY_CONTROLS`, or DECLARED in
    `ARMS_NO_SNIPPET_CAN_DECIDE` with the ordering fact that makes it
    undecidable. Adding an arm to the tool therefore cannot ship untested without
    this reddening — and the reverse direction catches a token deleted from the
    tool while its control row stays behind, which would otherwise make the table
    describe an arm that no longer exists.
    """
    module = _tool()
    arms = set(module.GRAPH_CAPABILITY_CYPHER)
    pinned = {arm.split("cypher:", 1)[1] for _, _, arm in CAPABILITY_CONTROLS}
    assert not pinned - arms, (
        "a control row pins an arm the tool no longer has — the table is "
        f"describing a guard that changed: {sorted(pinned - arms)}")
    unpinned = arms - pinned - ARMS_NO_SNIPPET_CAN_DECIDE
    assert not unpinned, (
        "these arms are neither pinned by a control row nor declared "
        f"undecidable: {sorted(unpinned)} — add a row to `CAPABILITY_CONTROLS` "
        "(with the LITERAL arm it must be caught by), or declare it in "
        "`ARMS_NO_SNIPPET_CAN_DECIDE` with the earlier arm that always wins")
    stale = ARMS_NO_SNIPPET_CAN_DECIDE - arms
    assert not stale, (
        f"ARMS_NO_SNIPPET_CAN_DECIDE names arms the tool does not have: {sorted(stale)}")
    # ⛔ THE SPLIT MUST BE A PARTITION (#14183 round-7, P3). Without this, a row
    # that DOES decide a declared-undecidable arm reds nothing: the declaration
    # just becomes a stale excuse, and `*1..` / `*..` / `shortestpath` sat in here
    # for a round while their own control rows would have pinned them. A token is
    # EITHER pinned by a row OR declared undecidable, never both.
    both = pinned & ARMS_NO_SNIPPET_CAN_DECIDE
    assert not both, (
        "these arms are BOTH pinned by a control row and declared undecidable — "
        f"the row proves the declaration false: {sorted(both)}. Drop the "
        "declaration (and the docstring reason that justified it)")


def test_the_census_has_exactly_one_row_producing_reader():
    """`read_after` is the ONE statement that produces feed rows.

    Every other reader is the store's own bookkeeping. If a second producing
    reader appears, a re-home must re-point TWO statements rather than one — the
    fact this count exists to surface before a re-point, not after.
    """
    module = _tool()
    serving = [r["reader"] for r in module.READERS if r["class"] == "serving"]
    assert serving == ["read_after"], serving


# ── 2. `journal_event` is not the home: producer contracts, executed ───────

def test_a_payload_only_emit_writes_the_store_and_no_journal_record(tmp_path):
    """THE refutation of "the journal already holds everything the feed serves".

    `sdk._emit_event("PointRetracted", {...})` is a SHIPPED call style — the turn
    hard-delete lane uses it verbatim (`sdk.py:3801`, "Two stores, one meaning
    each"). It is payload-only: no ``point``, no ``id``, no ``turns``. So:

    * the change feed gets a row (``type`` is in ``_GRAPH_EVENT_TYPES``),
    * the JSONL journal gets NOTHING — `_emit_event` returns before the journal
      half, by contract: *"Events with NONE of the three are skipped"*.

    A re-point onto `journal_event` would therefore drop this event from every
    subscriber while the stream still looked complete. That is not an off-by-one
    cursor; it is a missing event type, which is why `#13083` §6.3 refused to
    call the move a "feed re-pointing".

    ⛔ THE POSITIVE CONTROL IS PART OF THE PIN. Without it, `journal_rows == []`
    would also pass for a fixture whose journal path was never bound — the empty
    result would be the fixture's, not the producer's. The SECOND emit carries a
    point, so it must reach the journal; that is what proves the journal is
    wired and the silence above is the contract.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "events" / "events.jsonl"
    journal.parent.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "payload-only.db"),
                      event_log_path=str(journal))
    try:
        sdk._emit_event("PointRetracted", {"id": "p-never-created",
                                           "deleted": True})
        proj = sdk._get_proj()
        store_rows = proj.g.query(
            "MATCH (e:GraphEvent) RETURN properties(e)").result_set
        assert [row[0]["type"] for row in store_rows] == ["PointRetracted"], (
            "the store must hold the emit — otherwise this test proves nothing "
            "about the store/journal asymmetry")
        log = sdk._get_event_log()
        assert log is not None, "the fixture must have a journal for the pin to mean"
        payload_only_rows = log.read_all()

        # …the positive control: a point-carrying emit DOES reach the journal.
        sdk._create_point("statement", "a point that is journalled")
        control_rows = log.read_all()
    finally:
        sdk.close()

    assert payload_only_rows == [], (
        "a payload-only emit reached the journal — either the producer changed "
        "(then #13094's re-point became possible, and this pin must be "
        "re-derived) or the fixture grew a journal write it did not have")
    assert [row.get("type") for row in control_rows] == ["PointAdded"], (
        "the positive control did not reach the journal, so the empty result "
        "above proves nothing about the producer — the fixture's journal is not "
        f"wired (got {control_rows!r})")


def test_the_two_stores_seq_coordinates_are_deliberately_different():
    """No join on `seq` can ever be the cursor: the stores number differently.

    `:GraphEvent.seq` comes from the change feed's own allocator
    (`event_store.next_seq` → `:GraphEventMeta.last_seq`); `journal_event.seq` is
    the REPLAY journal's ordinal, allocated by `record_allocator` (`#12894`), and
    that migration states the two must not be conflated. The pin is the
    allocator's own declaration, so a future "they're both seq, just join them"
    shortcut reds here with the reason attached.
    """
    migration = (REPO_ROOT / "supabase" / "migrations"
                 / "20261116000001_record_cursor_allocator.sql").read_text()
    assert "does NOT authorise `:GraphEvent`\n-- to change coordinate" in migration, (
        "the #12894 migration's boundary line moved — re-derive this pin before "
        "a re-point, because it is the recorded decision that issued cursors "
        "stay on `:GraphEvent.seq`")


# ── 3. the tier mechanism cannot express the move (#4664) ──────────────────

def test_the_change_feed_has_no_record_home_in_the_projection_registries():
    """`NODE_TABLES`/`KEY_PROPERTY` do not name the shape — and must not start.

    Adding it is the route `#13083` §5.2 prescribes ("give it a `NODE_TABLES`
    record home with a tier column"), so this pin exists to make that route
    REFUSE with its reason instead of looking like unfinished work.
    """
    from tortoise import cache_projection as cp

    assert "GraphEvent" not in cp.NODE_TABLES, (
        "`:GraphEvent` is the change feed's STORE, not a projection of the "
        "record: `read_record_view` projects every `NODE_TABLES` label, so a "
        "record-sourced rebuild would try to re-CREATE the shape from the record "
        "view — the resurrection `#4664` establishes must not happen (a rebuild "
        "leaves the store EMPTY and carries only the watermark). The move is a "
        "STORE re-home, not a tier.")
    assert "GraphEvent" not in cp.KEY_PROPERTY, (
        "the record's key contract has no `:GraphEvent` key; `node_identity`'s "
        "CHECK restricts the labels, so a row could not satisfy the FK anyway")


def test_adding_the_shape_to_the_registries_would_project_it_and_then_refuse(
        monkeypatch):
    """The consequence, executed — this is why the route above is refused.

    With the two registries patched, `read_record_view` (the ONLY view a rebuild
    materialises from) reaches straight for the table: its SELECT is derived from
    `NODE_TABLES[label]` with no per-label special case — so the tier route puts
    `:GraphEvent` on the projection path, which `#4664` forbids. It then REFUSES
    the row, and the refusal is the second lock:

    * `UnknownRecordColumn` — the placement table has not classified
      `journal_event`'s columns, so nothing may silently become a node property.

    ⛔ The assertion is on THAT class, not a set of four: `KeyError` or
    `UnprojectableRecord` would fire for a different reason (a missing key
    contract, a NULL key), and accepting either would let an unrelated failure
    satisfy the "second lock" clause. The third lock — the key contract — is
    NAMED in the docstring, not executed: reaching it needs the column
    classification this test proves is missing.
    """
    from tortoise import cache_projection as cp
    from tortoise import record_projection as rp

    class _Cursor:
        def __init__(self):
            self.sql: list[str] = []
            self._rows: list = []

        def execute(self, sql, params):
            self.sql.append(sql)
            self._rows = ([{"org_id": "o", "graph_id": "g", "seq": "1",
                            "event_type": "PointAdded", "payload": "{}"}]
                          if "journal_event" in sql else [])

        def fetchall(self):
            return self._rows

        def description(self):
            return None

    class _Conn:
        def __init__(self):
            self.cursor_obj = _Cursor()

        def cursor(self):
            return self.cursor_obj

    monkeypatch.setattr(rp, "scope_connection", lambda conn, org: conn.cursor())
    monkeypatch.setitem(cp.NODE_TABLES, "GraphEvent", "journal_event")
    monkeypatch.setitem(cp.KEY_PROPERTY, "GraphEvent", "seq")
    conn = _Conn()

    with pytest.raises(cp.UnknownRecordColumn):
        cp.read_record_view(conn, org_id="o", graph_id="g", labels=("GraphEvent",))

    selects = [sql for sql in conn.cursor_obj.sql
               if sql.startswith("SELECT * FROM public.")]
    assert any("public.journal_event" in sql for sql in selects), (
        "the record view must be the label-agnostic reader this test claims — "
        f"it issued {conn.cursor_obj.sql!r}")


# ── 4. the superset verdict, and the exit codes that carry "no verdict" ────

def test_the_superset_verdict_names_a_missing_type_and_a_missing_row():
    """The verb's verdict, on synthetic rows — the logic that will run on a tenant.

    Two deficits, deliberately different, because they carry different remedies:

    * a TYPE the record never carries (a subscriber sees a complete-looking,
      silently SHORT stream — the failure `#13083` §6.3 calls the worse one);
    * a ROW inside a type the record does carry (a lost event, not a lost
      vocabulary).
    """
    module = _tool()
    graph_rows = [
        {"type": "PointAdded", "payload": {"id": "a"}},
        {"type": "PointAdded", "payload": {"id": "b"}},
        {"type": "PointRetracted", "payload": {"id": "b", "deleted": True}},
    ]
    journal_rows = [{"type": "PointAdded", "payload": {"id": "a"}}]
    verdict = module.superset(graph_rows, journal_rows)

    assert verdict["verdict"] == "not_covered"
    assert verdict["missing_types"] == ["PointRetracted"]
    assert verdict["per_type"]["PointAdded"] == {
        "graph_rows": 2, "matched": 1, "unmatched": 1, "record_has_type": True}
    assert verdict["missing_rows"] == 2
    assert "PRODUCERS DISAGREE" in verdict["caveat"], (
        "the verdict must carry its own limit: a tenant-level `covered` is not "
        "evidence that a re-point is safe")


def test_a_type_the_record_carries_under_another_name_is_a_named_deficit():
    """`PointRetracted` vs `EntityMutated` must not read as "covered" NOR as "absent".

    The hard-delete lane announces ONE removal to two surfaces with different
    names AND different payload shapes (`sdk.py:3801`). Comparing the names
    literally would say "the record has no such type" (false — it has the event
    under another name); comparing the payloads would be meaningless. So the
    verdict names it: `mapped_types`, `row_coverage: not_comparable`, and
    `not_covered` — because "we cannot compare it" is not "it is covered".
    """
    module = _tool()
    graph_rows = [{"type": "PointRetracted", "payload": {"id": "p", "deleted": True}}]
    journal_rows = [{"type": "EntityMutated", "payload": {"id": "p", "op": "delete"}}]
    verdict = module.superset(graph_rows, journal_rows)

    assert verdict["verdict"] == "not_covered"
    assert verdict["mapped_types"] == ["PointRetracted"]
    assert verdict["missing_types"] == []
    assert verdict["missing_rows"] == 0, (
        "a mapped type must not ALSO be counted as missing rows — it is a "
        "separate deficit class")
    assert verdict["per_type"]["PointRetracted"] == {
        "graph_rows": 1, "record_has_type": False,
        "record_has_alias_type": ["EntityMutated"],
        "row_coverage": "not_comparable"}


def test_an_empty_record_is_not_coverage_either():
    """Symmetry: an unarmed or mistyped RECORD scope is not "the record is short".

    Round 2 found the claim ("an empty feed on EITHER side") was true of the
    graph side only: an empty record with a non-empty graph was `not_covered`
    (exit 1), which tells the operator to go and backfill a record that was
    never asked — the same typo class the graph guard exists for.
    """
    module = _tool()
    verdict = module.superset([{"type": "PointAdded", "payload": '{"id": "a"}'}], [])
    assert verdict["verdict"] == "no_verdict"
    assert verdict["record_events"] == 0


def test_an_empty_feed_is_not_coverage():
    """The vacuous PASS: an empty multiset is a subset of everything.

    A purged feed, a mistyped graph name and a store that answered with nothing
    all produce zero graph rows — and the naive arithmetic over zero rows answers
    `covered`, i.e. "the record holds every event the graph has" for a graph that
    has none. `no_verdict` is the honest answer, and the verb exits 2 for it.
    """
    module = _tool()
    verdict = module.superset([], [])
    assert verdict["verdict"] == "no_verdict"
    assert verdict["graph_events"] == 0


def test_a_payload_is_compared_by_value_not_by_its_storage_spelling():
    """The graph stores the payload as JSON TEXT, the record as `jsonb`.

    Digesting the raw spelling would report every row as missing for a
    whitespace/key-order difference — a false negative in the one instrument
    that decides whether a move is safe, which is the direction that matters.
    """
    module = _tool()
    graph_rows = [{"type": "PointAdded", "payload": '{"id": "a", "n": 1}'}]
    journal_rows = [{"type": "PointAdded", "payload": {"n": 1, "id": "a"}}]
    assert module.superset(graph_rows, journal_rows)["verdict"] == "covered"


# ── 5. paging: the two shapes that break a keyset read ─────────────────────

class _StubGraph:
    """A graph whose `:GraphEvent` rows are a scripted list — no server.

    ⛔ IT ASSERTS ON THE MODULE'S OWN STATEMENTS, and that is the whole point
    (review round 2, P2): an earlier version re-implemented the composite filter
    here, so reverting the module's `WHERE` to a strict `seq > $after`, or
    dropping the `event_id` tie-break from its `ORDER BY`, left the test GREEN —
    the test was pinning itself. The two `assert`s below make the SQL under test
    part of the fixture, so a one-clause simplification reds.
    """

    def __init__(self, rows):
        self.rows = rows
        self.pages = 0

    def query(self, statement, params=None):
        self.pages += 1
        if self.pages > 50:
            raise AssertionError("the reader never advanced — an unbounded loop")
        # the completeness guards run AFTER the walk; answer them from the data
        if "RETURN count(e)" in statement:
            if "e.seq IS NULL" in statement:
                return type("_R", (), {
                    "result_set": [[sum(1 for r in self.rows if r[0] is None)]]})()
            return type("_R", (), {"result_set": [[len(self.rows)]]})()
        assert "ORDER BY e.seq ASC, e.event_id ASC" in statement, (
            "the page order must tie-break on `event_id` — without it two rows "
            f"sharing a seq can be returned in an order the cursor cannot resume "
            f"from, losing one. Statement: {statement!r}")
        params = params or {}
        limit = params.get("limit")
        after = params.get("after")
        last_id = params.get("last_id")
        if after is None:
            candidates = list(self.rows)
        else:
            assert "e.seq > $after OR (e.seq = $after AND e.event_id > $last_id)" \
                in statement, (
                "the cursor must be COMPOSITE — a strict `seq > $after` drops rows "
                f"sharing a seq at a page boundary. Statement: {statement!r}")
            # a NULL seq is UNREACHABLE by the cursor (that is the defect the
            # completeness guard exists for), so the stub emulates the engine:
            # such a row never appears on a later page.
            candidates = [r for r in self.rows
                          if r[0] is not None
                          and (r[0] > after or (r[0] == after and r[2] > last_id))]
        # the engine sorts NULL LAST; Python cannot compare None with int, so the
        # key spells that order out.
        candidates.sort(key=lambda r: (r[0] is None, r[0] if r[0] is not None else 0,
                                       str(r[2])))
        page = candidates[:limit] if limit else candidates
        return type("_R", (), {"result_set": page})()


class _StubDB:
    def __init__(self, graph):
        self.graph = graph
        self.selected: list[str] = []
        self.connection = type("_C", (), {
            "execute_command": staticmethod(lambda *a: ["tortoise", "g_real"]),
        })()

    def select_graph(self, name):
        self.selected.append(name)
        return self.graph


def test_a_duplicate_seq_at_a_page_boundary_is_not_dropped():
    """`seq` is NOT unique (only `event_id` is), so a strict `>` cursor loses rows.

    A lost row makes the comparison UNDER-report the graph's population — the
    direction that produces a false `covered`. The reader pages with a STRICT
    COMPOSITE cursor — `e.seq > $after OR (e.seq = $after AND e.event_id > $last_id)`
    — and dedupes, so all three rows survive a two-row page.

    ⛔ THE PAYLOAD OF ROWS 2 AND 3 IS DELIBERATELY IDENTICAL (#14183 round-4
    review, P2). The dedupe key is `(seq, event_id, type, digest(payload))`, and
    with distinct payloads the OLD key — the one without `event_id` — also keeps
    all three rows, so reverting `event_id` out of the key reddened NOTHING. Two
    rows sharing `(seq, type, payload)` with distinct `event_id`s are the
    multiset members a payload-keyed dedupe merges. Under the old key that merge
    happens at the PAGE BOUNDARY: the second page's only row is already `seen`,
    so `added` stays 0 and the `added == 0` arm of `_graph_rows` refuses
    (`this page added no new row`) rather than returning 2 rows against a total
    of 3 — a short read is refused, never reported. The assertion below is on all
    three surviving, so the revert reds here.
    """
    module = _tool()
    rows = [
        (1, "PointAdded", "e1", '{"id": "a"}'),
        (2, "PointAdded", "e2", '{"id": "b"}'),
        (2, "PointAdded", "e3", '{"id": "b"}'),
    ]
    db = _StubDB(_StubGraph(rows))
    got = module._graph_rows(db, "g_real", batch=2)
    assert [row["event_id"] for row in got] == ["e1", "e2", "e3"]


def test_a_fractional_seq_advances_instead_of_looping():
    """A non-integer `seq` is a MEASURED shape in this store, not a hypothetical.

    `webhook_delivery.py` records a hand-written `3.5`, and an `int()` cursor
    would re-fetch it forever. The cursor keeps the server's own value, so the
    read terminates with every row.
    """
    module = _tool()
    rows = [
        (1, "PointAdded", "e1", '{"id": "a"}'),
        (2.5, "PointAdded", "e2", '{"id": "b"}'),
        (3.5, "PointAdded", "e3", '{"id": "c"}'),
    ]
    got = module._graph_rows(_StubDB(_StubGraph(rows)), "g_real", batch=2)
    assert [row["event_id"] for row in got] == ["e1", "e2", "e3"]


def test_a_row_without_an_event_id_refuses_instead_of_truncating():
    """The composite cursor cannot advance past a row with no `event_id`.

    Named refusal, never a truncation: the alternative is a partial read reported
    as a verdict (`append_event` always writes an `event_id`, so this is a
    hand-written or repaired row, and the message names `read_after` as the reader
    that does page on `seq` alone).
    """
    module = _tool()
    rows = [
        (1, "PointAdded", "e1", '{"id": "a"}'),
        (2, "PointAdded", None, '{"id": "b"}'),
        (3, "PointAdded", "e3", '{"id": "c"}'),
    ]
    with pytest.raises(module.Unpageable) as refused:
        module._graph_rows(_StubDB(_StubGraph(rows)), "g_real", batch=2)
    assert "event_id" in str(refused.value)

    # ⛔ A PRESENT NON-STRING id is the SAME refusal, and the message must say so: the
    # predicate is `not isinstance(cursor_id, str)`, so "carries no `event_id`" is
    # false for this row (round-9 review, finding 4). Both arms are pinned because
    # relaxing the predicate to `cursor_id is None` left the suite green while a
    # present non-string id became a mixed-type `e.event_id > $last_id` compare —
    # measured to return nothing, i.e. a short read.
    mixed = [
        (1, "PointAdded", "e1", '{"id": "a"}'),
        (2, "PointAdded", 12345, '{"id": "b"}'),
        (3, "PointAdded", "e3", '{"id": "c"}'),
    ]
    with pytest.raises(module.Unpageable) as refused_int:
        module._graph_rows(_StubDB(_StubGraph(mixed)), "g_real", batch=2)
    assert "STRING `event_id`" in str(refused_int.value), str(refused_int.value)
    assert "not a string" in str(refused_int.value), str(refused_int.value)


def test_a_graph_the_server_does_not_hold_is_refused_before_it_is_read():
    """A read against an absent graph CREATES it (#11113, measured in this repo).

    So the verb cannot "just look": it must refuse a name the server does not
    hold, or a read-only instrument writes — and then compares an empty graph it
    has just minted.

    ⛔ THE PIN IS ON THE READER, NOT ON THE GUARD (review round 2, P2). Round 1
    pinned `_require_graph` in isolation, so deleting the call from `residency`
    (and never adding one to `superset`, which is the P1 that survived) left the
    test GREEN. `_graph_rows` is what both verbs actually call, so the
    assertion is on it — and on `select_graph` never being reached.
    """
    module = _tool()
    db = _StubDB(_StubGraph([]))
    with pytest.raises(module.Unpageable) as refused:
        module._graph_rows(db, "g_never_existed")
    assert "CREATES it" in str(refused.value)
    assert db.selected == [], "the refusal must happen BEFORE any graph is bound"


def test_the_superset_verb_refuses_an_unknown_graph_too(monkeypatch, capsys):
    """The verb an operator points at a TENANT must not mint it either — and it
    must refuse BEFORE paying for the journal.

    `residency` was guarded and `superset` was not (review round 2, P1): the
    guard sat in one verb, so the one whose verdict gates a re-home was the one
    that would create an empty graph from a `--graph` typo and then compare it.

    ⛔ THE ORDERING HALF IS PINNED HERE TOO (review round 3, P2). Round 2 wrote
    this assertion and then set `_record_connection` a SECOND time, overwriting
    it — so it never ran, and the code contradicted it anyway: `main()` opened
    and paged the whole tenant journal before `_graph_rows` ran the refusal.
    A store that raises is swallowed by `main()`'s `except Exception` and still
    exits 2, so the exit code cannot discriminate; the CALL RECORD and the
    message are what can. Both are asserted below.
    """
    module = _tool()
    db = _StubDB(_StubGraph([]))
    monkeypatch.setattr(module, "_graph_connection", lambda uri: db)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
    # Set EXPLICITLY: without it `main()` stops at its "no record DSN" arm and the
    # graph arm is never reached, so every assertion here would be vacuous.
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://nowhere/record")
    read: list[tuple[str, str]] = []

    def _must_not_read(dsn, org):
        read.append((dsn, org))
        raise AssertionError("the record must not be read for a graph that does "
                             "not exist")

    monkeypatch.setattr(module, "_record_connection", _must_not_read)
    assert module.main(["superset", "--graph", "g_never_existed", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    err = capsys.readouterr().err
    assert read == [], "superset read the record for a graph the server does not hold"
    assert "CREATES it" in err, (
        "the refusal must be the GRAPH arm's, not a caught record error: " + err)
    assert "NO VERDICT" in err
    assert db.selected == [], "superset bound a graph the server does not hold"


def test_the_residency_verb_refuses_an_unknown_graph_before_reading_totals(
        monkeypatch):
    """`residency` reads node TOTALS before `_graph_rows`, so it needs its own guard.

    ⛔ THE PARAGRAPH AND THE CODE MUST AGREE (#14183 round-4 review, P2). The
    evidence doc says the refusal lives in the ONE reader both verbs call — but
    `residency`'s `MATCH (n)` totals read runs BEFORE `_graph_rows`, and a read
    against an absent graph CREATES it (`#11113`), so `_graph_rows` would then
    find "present" the very graph the totals read had just minted. The verb-level
    `_require_graph` is therefore not redundant, and this pin is why deleting it
    reds.

    The assertion is on `select_graph` never being reached: with the verb-level
    guard removed, the totals query binds the graph and `db.selected` is non-empty.
    """
    module = _tool()
    db = _StubDB(_StubGraph([]))
    monkeypatch.setattr(module, "_graph_connection", lambda uri: db)
    with pytest.raises(module.Unpageable) as refused:
        module.residency("docker://:@127.0.0.1:1/x", "g_never_existed")
    assert "CREATES it" in str(refused.value)
    assert db.selected == [], (
        "residency bound a graph the server does not hold — its totals read would "
        "have minted the empty graph it then reports on")


class _RecordCursor:
    """One static page, then empty — enough for `record_rows` to finish."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._served = False

    def execute(self, *_a, **_k):
        return None

    def fetchall(self):
        if self._served:
            return []
        self._served = True
        return self._rows


class _RecordConnection:
    def __init__(self, rows):
        self._cur = _RecordCursor(rows)

    def cursor(self):
        return self._cur

    def close(self):
        return None


def test_the_no_verdict_message_names_the_side_that_was_EMPTY(monkeypatch, capsys):
    """A refusal must say WHICH side was empty, or it points at the wrong store.

    Round 3 (P2): the verb refuses an empty RECORD as well as an empty graph, but
    its only operator-facing sentence still said "the graph holds no `:GraphEvent`
    rows". For the case that widening introduces — a mistyped `--org-id`/
    `--graph-id` or a never-armed scope, against a graph holding N events — the
    verb told the operator the graph was empty and to go backfill it, which is
    the mirror of the misdirection the evidence doc forbids.

    Both directions are driven, so the test cannot pass by pinning one fixed
    string: an empty graph must be called a graph, an empty record a record.
    """
    module = _tool()

    def _run(graph_rows, record_rows_):
        db = _StubDB(_StubGraph(graph_rows))
        monkeypatch.setattr(module, "_graph_connection", lambda uri: db)
        monkeypatch.setattr(module, "_record_connection",
                            lambda dsn, org: _RecordConnection(record_rows_))
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://nowhere/record")
        code = module.main(["superset", "--graph", "g_real", "--org-id", "o",
                           "--graph-id", "g"])
        return code, capsys.readouterr().err

    # the graph holds rows, the RECORD holds none -> name the record
    code, err = _run([(1, "PointAdded", "e1", '{"id": "a"}')], [])
    assert code == 2, err
    assert "RECORD" in err, err
    assert "--org-id" in err, (
        "the empty-record arm must name the scope an operator would have got "
        "wrong: " + err)
    assert "the graph holds no" not in err, (
        "the graph holds a row, so telling the operator it is empty sends them "
        "to backfill the store that is fine: " + err)

    # control: an EMPTY graph still names the graph
    code, err = _run([], [(1, "PointAdded", '{"id": "a"}')])
    assert code == 2, err
    assert "the graph holds no" in err, err
    assert "RECORD" not in err, err


def test_a_null_seq_row_refuses_instead_of_shortening_the_read():
    """A NULL `seq` can never satisfy a `seq` cursor, so the walk must not call it an end.

    Measured in review round 2: with `batch=1` a NULL-seq row was silently
    skipped and the shortened read was `covered`; with the whole feed in one page
    the same row REFUSED. Both directions from one input is the signature of a
    guard that is not on the path, so the guard is now a count, after the walk.
    """
    module = _tool()
    rows = [
        (1, "PointAdded", "e1", '{"id": "a"}'),
        (None, "PointAdded", "e2", '{"id": "b"}'),
    ]
    with pytest.raises(module.Unpageable) as refused:
        module._graph_rows(_StubDB(_StubGraph(rows)), "g_real", batch=1)
    assert "no `seq`" in str(refused.value)


def test_a_short_read_is_refused_even_when_the_pages_look_exhausted():
    """The count is what proves completeness: pages alone cannot.

    A `seq` stored as a STRING is the shape that breaks it — the engine orders
    strings before numbers, so the page that ends the string block puts a STRING on
    the cursor, and `seq > $after` then matches no NUMERIC row: the walk ends early
    with a page that looks like the end of the feed (measured on the lane,
    `v > '10'` returns nothing while `v > 5` returns the numeric rows, so it is the
    STRING cursor that strands, never a numeric one). The stub cannot stand in for
    that shape: `_StubGraph`'s sort key raises `TypeError` comparing `str` with
    `int`, so a mixed-type `seq` is unrepresentable here and this arm drives the
    count guard directly by making the walk's own pages stop short.
    """
    module = _tool()

    class _Truncating(_StubGraph):
        def query(self, statement, params=None):
            if "RETURN count(e)" in statement and "e.seq IS NULL" not in statement:
                return type("_R", (), {"result_set": [[3]]})()
            return super().query(statement, params)

    rows = [(1, "PointAdded", "e1", '{"id": "a"}'),
            (10, "PointAdded", "e2", '{"id": "b"}')]
    with pytest.raises(module.Unpageable) as refused:
        module._graph_rows(_StubDB(_Truncating(rows)), "g_real", batch=2000)
    assert "the cursor reached" in str(refused.value)


# ── 6. the exit codes, and the instrument's own selection ─────────────────

def test_a_read_that_could_not_run_reports_no_verdict(monkeypatch, capsys):
    """Exit 2, never "covered": an unrun read is not an answer.

    Both arms (no graph URI, no record DSN) are checked, because the verb's
    worst failure mode is printing a reassuring `covered` it never measured.
    """
    module = _tool()
    for name in ("TORTOISE_DB_URI", "TORTOISE_RECORD_DSN"):
        monkeypatch.delenv(name, raising=False)

    assert module.main(["residency", "--graph", "g_x"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
    assert module.main(["superset", "--graph", "g_x", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err


def test_a_store_that_refused_exits_2_and_never_1(monkeypatch, capsys):
    """A read that FAILED must not share exit 1 with "not covered".

    This is not hypothetical politeness: `main()` returns 1 for a produced
    `not_covered` verdict, so an uncaught store exception would leave Python's
    default exit status — **also 1** — and a caller gating on the exit would read
    "the record is short" from a record it never read. The third arm is
    `psycopg2` ABSENT, which used to raise `SystemExit` (a `BaseException`) out
    of this guard and exit 1 for the same reason; it is now a `RecordUnavailable`.
    """
    module = _tool()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://nowhere/record")
    # ⛔ THE GRAPH SIDE MUST SUCCEED for the two `superset` arms below to say
    # anything about the RECORD (review round 3, P2). `main()` now refuses an
    # unknown graph BEFORE it opens the record, so without this stub both arms
    # would exit 2 from the graph read and the `RecordUnavailable` path — the one
    # this test exists for — would never run.
    monkeypatch.setattr(module, "_graph_connection",
                        lambda uri: _StubDB(_StubGraph([])))

    def _refuse(*_a, **_k):
        raise RuntimeError("the store refused the read")

    monkeypatch.setattr(module, "residency", _refuse)
    assert module.main(["residency", "--graph", "g_x"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    monkeypatch.setattr(module, "_record_connection", _refuse)
    assert module.main(["superset", "--graph", "g_real", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    assert "the store refused the read" in capsys.readouterr().err

    def _no_driver(*_a, **_k):
        raise module.RecordUnavailable("psycopg2 is not installed")

    monkeypatch.setattr(module, "_record_connection", _no_driver)
    assert module.main(["superset", "--graph", "g_real", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    err = capsys.readouterr().err
    assert "psycopg2 is not installed" in err, (
        "the RECORD arm must be the one that ran — otherwise this asserts the "
        "graph read refused, not the missing driver: " + err)
    assert "NO VERDICT" in err


def test_the_residency_measure_script_refuses_a_graph_outside_its_prefix(
        monkeypatch, capsys):
    """The measurement's refusal is exit 2, and it happens before any graph touch.

    #14183 round-4 review (P3): `measure_graphevent_residency.py` moved its
    prefix refusal from `raise SystemExit("refusing…")` (which exits **1**, the
    code the sibling verb reserves for a produced verdict) to a stderr message +
    `SystemExit(2)`. Nothing pinned that, and the doc declared exit-2 semantics
    for the `superset` verb only, so the change was invisible to both.

    A refusal is not a measurement: `2` is "could not run", `1` is "ran, and the
    answer is no" — and a caller gating on the exit code must not read the first
    as the second.
    """
    module = _measure()
    touched: list[str] = []

    class _NeverUsed:
        """Any use is a failure: the refusal must precede every graph command."""

        class connection:
            @staticmethod
            def execute_command(*a, **_k):
                touched.append("execute_command")
                raise AssertionError("the refusal must not touch a graph")

        @staticmethod
        def select_graph(name):
            touched.append(name)
            raise AssertionError("the refusal must not bind a graph")

    monkeypatch.setattr(module, "_connection", lambda uri: _NeverUsed())
    with pytest.raises(SystemExit) as refused:
        module.measure("docker://:@127.0.0.1:1/x", "tortoise", [10], False)
    assert refused.value.code == 2, (
        f"a refusal is exit 2, not {refused.value.code!r} — `1` is the sibling "
        "verb's 'a verdict was produced' code, so a refusal sharing it reads as "
        "an answer")
    err = capsys.readouterr().err
    assert "refusing" in err and module.GRAPH_PREFIX in err, err
    assert touched == [], "the refusal touched a graph it had just refused"


def test_a_run_that_COULD_NOT_RUN_is_exit_2_and_never_a_bare_traceback(
        monkeypatch, capsys):
    """`1` is RESERVED for a produced verdict, so an uncaught failure must not reach it.

    #14183 review round 11 (measured): `main()` called `measure()` unguarded, so
    every ordinary "could not run" — an unreachable server (`ConnectionError`),
    a refused query — escaped as a traceback, and Python exits **1** on an
    uncaught exception. `README` §6 declares `1` "reserved for a produced
    verdict", so the shell could not tell a crash from a measurement that
    returned "no". Only the prefix refusal and the refused-schema arm were
    mapped to 2, and only those two were pinned.

    The failure is injected at `measure()` on purpose: what is graded is
    `main()`'s exit contract, not the transport's.
    """
    module = _measure()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")

    def _cannot_run(*_args, **_kwargs):
        raise ConnectionError("Error 111 connecting to 127.0.0.1:1. Connection refused.")

    monkeypatch.setattr(module, "measure", _cannot_run)
    code = module.main(["--sizes", "10"])
    assert code == 2, (
        f"a run that could not run must exit 2, not {code!r} — `1` is reserved for "
        "a produced verdict, and an uncaught exception exits 1")
    err = capsys.readouterr().err
    assert "could not run" in err, err
    assert "Connection refused" in err, err


def test_the_measure_scripts_constraint_names_the_graph_key_not_a_constraint():
    """`GRAPH.CONSTRAINT CREATE` takes the GRAPH KEY, not a constraint name (#14183 r9).

    The pre-fix spelling put `tortoise_ge_uid` — a NAME — in that slot, so the module
    command MINTED that graph and then failed its own precondition (`missing
    supporting exact-match index`), which a census row published as a server limit.
    Measured: reverting the slot to the old spelling left this whole suite green, so
    the slot and the argv are graded here rather than trusted.
    """
    module = _measure()
    commands = [stmt for kind, stmt in module.SCHEMA_STATEMENTS if kind == "command"]
    assert len(commands) == 1, f"one module command expected, got {commands}"
    template = commands[0]
    tokens = template.split()
    assert tokens[:2] == ["GRAPH.CONSTRAINT", "CREATE"], template
    assert tokens[2] == "{graph}", (
        "the token after CREATE is the GRAPH KEY; a constraint NAME there mints a "
        f"graph that carries no `event_id` index — template: {template!r}")
    argv = module.command_tokens(template, "probe_13094_measure")
    assert argv[2] == "probe_13094_measure", argv
    assert not any("{graph}" in token for token in argv), argv
    # ⛔ THE KEY STAYS ONE ARGUMENT: building the argv by re-splitting a formatted
    # string would shift every following token when the key carries whitespace.
    assert module.command_tokens(template, "a b")[2] == "a b", (
        "a graph key containing whitespace must remain a single argv token")


def test_a_refused_schema_statement_refuses_the_run(monkeypatch, capsys):
    """A run whose schema did not install is NOT a measurement — and the ARGV is graded.

    ⛔ TWO ARMS ON ONE FIXTURE (#14183 review round 11). (a) THE GUARD: a refused schema
    statement marks the run `schema_complete: False` and `main()` exits 2, so a
    population measured without the schema it claims cannot be quoted as one —
    deleting the guard left this suite green. (b) THE ISSUANCE PATH, not the helper:
    `test_the_measure_scripts_constraint_names_the_graph_key_not_a_constraint` grades
    `command_tokens` in isolation, so reverting `measure()` to
    `execute_command(*issued.split())` also left the suite green — the "pinned in
    isolation" shape this branch already fixed for `_require_graph` (round 2). The
    argv captured AT THE CALL SITE is asserted here, with a key carrying whitespace,
    which is the one input on which the two spellings differ.
    """
    module = _measure()
    argv: list[tuple] = []

    class _Conn:
        @staticmethod
        def execute_command(*args):
            argv.append(args)
            if args and args[0] == "GRAPH.CONSTRAINT":
                raise RuntimeError("missing supporting exact-match index")
            if args[:2] == ("INFO", "memory"):
                return {"used_memory": 1000}
            if args and args[0] == "GRAPH.MEMORY":
                return ["total_graph_sz_mb", 1]
            return "OK"

    class _Graph:
        @staticmethod
        def query(statement, params=None):
            return type("_R", (), {"result_set": [[1]]})()

    class _DB:
        connection = _Conn()

        @staticmethod
        def select_graph(name):
            return _Graph()

    monkeypatch.setattr(module, "_connection", lambda uri: _DB())
    result = module.measure("docker://x", "probe_13094 a", [10], False)

    assert result["schema_complete"] is False, (
        "a refused schema statement must mark the run incomplete, or the slope it "
        "publishes claims a schema the population does not carry")
    constraint = [call for call in argv if call and call[0] == "GRAPH.CONSTRAINT"]
    assert constraint, argv
    assert constraint[-1] == (
        "GRAPH.CONSTRAINT", "CREATE", "probe_13094 a", "UNIQUE", "NODE",
        "GraphEvent", "PROPERTIES", "1", "event_id"), (
        "the argv must carry the graph key as ONE token — re-splitting the formatted "
        f"statement shifts every following argument; got {constraint[-1]!r}")

    # (b2) THE EXIT CONTRACT: a refused schema is exit 2, never a published slope.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://x")
    monkeypatch.setattr(
        module, "measure",
        lambda *a, **k: {"schema_complete": False,
                         "schema": [{"installed": False,
                                     "statement": "GRAPH.CONSTRAINT CREATE …"}]})
    assert module.main([]) == 2, (
        "a run with a refused schema statement is \"could not run\" (2), not a "
        "measurement")
    err = capsys.readouterr().err
    assert "REFUSED" in err and "must NOT be quoted" in err, err


def test_a_complete_schema_still_publishes_a_measurement(monkeypatch):
    """The other arm of `main()`'s schema gate (#14183 review round 10, P2).

    `test_a_refused_schema_statement_refuses_the_run` grades the REFUSING arm only, and
    a gate asserted in one arm passes just as well when it refuses ALWAYS: a `main()`
    that returned 2 unconditionally — no measurement ever published — leaves that test
    green. This is the arm that reds it (measured).
    """
    module = _measure()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://x")
    monkeypatch.setattr(module, "measure", lambda *_a, **_k: {
        "schema_complete": True, "schema": [{"installed": True, "statement": "s"}]})
    assert module.main([]) == 0, (
        "a run whose schema installed IS a measurement — 0, never a refusal")


def test_the_declared_event_types_are_the_sdks_own():
    """The census names the store's type vocabulary; it must BE the store's.

    A copy that drifts would let the tool's report describe an emit set the SDK
    does not write — and the per-type verdict is the whole report.
    """
    module = _tool()
    from tortoise.sdk import _GRAPH_EVENT_TYPES

    assert module.GRAPH_EVENT_TYPES == _GRAPH_EVENT_TYPES


def test_the_reader_census_is_json_serialisable_and_states_its_verdict():
    """The verb a reviewer or an operator runs must emit the table, not prose."""
    module = _tool()
    report = module.readers_report()
    json.dumps(report)  # must not raise
    assert report["counts"] == {"serving": 1, "maintenance": 15}
    assert len(report["readers"]) == 16
    assert report["graph_capability_readers"] == []


def test_a_capability_reader_makes_the_verdict_say_so(monkeypatch):
    """The DERIVED verdict must be exercised in its non-empty branch (round 3, P2).

    The PR's stated fix is that the verdict is derived from `capability_readers`
    instead of being a fixed sentence, so it cannot print "no reader needs a graph
    capability" beside a non-empty `graph_capability_readers`. Nothing pinned that:
    `READERS` holds no capability row, so the empty-branch string and the pre-fix
    literal produce byte-identical output — reverting the derivation left every
    row green (agent-infra#3442's class: an untested fix).

    This injects ONE capability-bearing row and asserts the verdict names it, so
    reverting to the literal reds here.
    """
    module = _tool()
    capability_row = {
        "file": "tortoise/example.py",
        "line": "1",
        "reader": "injected_traversal",
        "snippets": ("MATCH (e:GraphEvent)-[r:TOUCHED]->(p:Point) RETURN p",),
        "needs": "a hop from the event to the point",
        "class": "maintenance",
        "why": "fixture: the capability is in the Cypher, not the prose",
    }
    monkeypatch.setattr(module, "READERS", (*module.READERS, capability_row))
    report = module.readers_report()

    assert report["graph_capability_readers"] == [
        {"file": "tortoise/example.py", "line": "1",
         "reader": "injected_traversal", "capability": "cypher:-["}], (
        "the census must surface a capability row at its class, and name the "
        f"shape that made it one: {report['graph_capability_readers']!r}")
    assert "injected_traversal" in report["verdict"], (
        "the verdict must name the reader that needs the capability, not print "
        f"a reassuring sentence beside a non-empty list: {report['verdict']!r}")
    assert "cypher:-" in report["verdict"], report["verdict"]
    assert "no reader needs a graph capability" not in report["verdict"]
    json.dumps(report)  # the injected form must still serialise


def test_the_committed_census_is_the_verbs_own_output():
    """`readers.json` is EVIDENCE, so it must be the verb's bytes, not a memory.

    The doc cites `readers.json` as the run that produced its table. A committed
    copy that a later edit to `READERS` does not carry would let the artifact and
    the code disagree while every other pin stayed green — the drift class this
    census exists to expose, applied to the census itself.
    """
    module = _tool()
    committed = json.loads(
        (REPO_ROOT / "docs" / "evidence" / "13094-graphevent" / "readers.json"
         ).read_text(encoding="utf-8"))
    # Round-trip the report through JSON before comparing: `READERS` holds tuples
    # where the file holds lists, and that is a serialisation detail rather than
    # drift. Comparing the SERIALISED form is what makes this a pin on the
    # artifact rather than on a Python container type.
    assert committed == json.loads(json.dumps(module.readers_report())), (
        "`readers.json` no longer matches the verb — regenerate it with "
        "`uv run python tools/graphevent_home.py readers > "
        "docs/evidence/13094-graphevent/readers.json` rather than hand-editing")


def test_the_evidence_docs_pin_count_is_this_files_own_count():
    """The doc's "N pins" must BE the number of pins — the count drifted once.

    Review round 3 (P3): the README said "24 pins" while this file held 26, and its
    enumeration omitted the drift pin above. The convention is per-test-function
    (the pre-fix doc said 21 against a file of 21), so the number is checkable
    rather than rhetorical — and a hand-maintained count that nothing reads is a
    claim that drifts on the next test anyone adds. Counting the file's own
    `def test_` lines is what makes it a measurement.
    """
    readme = (REPO_ROOT / "docs" / "evidence" / "13094-graphevent" / "README.md"
              ).read_text(encoding="utf-8")
    declared = re.search(
        r"`tests/test_13094_graphevent_home\.py` \| (\d+) pins", readme)
    assert declared, "the README's pin-count row is gone — restore it as `N pins:`"
    here = REPO_ROOT / "tests" / "test_13094_graphevent_home.py"
    actual = sum(1 for line in here.read_text(encoding="utf-8").splitlines()
                 if line.startswith("def test_"))
    assert int(declared.group(1)) == actual, (
        f"the README declares {declared.group(1)} pins but this file holds {actual}"
        " — update the count and the enumeration in"
        " docs/evidence/13094-graphevent/README.md §6")


def test_the_noise_stripper_is_the_write_gates_grammar_plus_one_backtick_branch():
    """The guard's pre-pass is the write gate's comment/literal grammar, one branch wider.

    WHY THIS IS PINNED RATHER THAN TRUSTED. `tools/graphevent_home.py` carries its
    own copy of that grammar (`_LITERAL_COMMENT_OR_IDENT_RE`) instead of importing
    `tortoise.projection`'s `_GATE_LITERAL_OR_COMMENT_RE`, because it needs ONE
    alternation in which a backtick-quoted identifier is UNWRAPPED: the gate KEEPS
    the identifier (so a `//` inside `` `a//b` `` opens a line comment that eats the
    rest of the statement, pattern and arms included) and the sibling
    `_LITERAL_OR_COMMENT_RE` DELETES it (so `` CALL `algo.pageRank` `` loses the very
    name the `call algo` arm reads). A copy is a drift risk, so the agreement is
    MEASURED: on every input with no backtick in it the two grammars must produce
    the same normalised haystack, and a change to the gate's spelling reds here
    rather than silently widening the guard's blind spot.

    NON-VACUOUS, and it has two arms that fail in opposite directions: the corpus
    loop reds if the copy drifts, and the `delta` assertion below reds if the copy
    ever collapses back to the gate's own treatment of backticks — which is what a
    "just import it" tidy-up would do.
    """
    from tortoise.projection import _GATE_LITERAL_OR_COMMENT_RE as gate

    tool = _tool()
    corpus = (
        "MATCH (e)-[r:T]->(p) RETURN p",
        "MATCH (e) - /* hop */ [r:T] - (p) RETURN p",
        "MATCH (e)-//hop\n[r:T]-(p) RETURN p",
        "MATCH (e) WHERE e.p = 'a // not a comment' RETURN e",
        'MATCH (e) WHERE e.p = "a /* not a comment */" RETURN e',
        "MATCH (e) WHERE e.url = 'https://x/y' AND e.z = '/*' RETURN e",
        "CALL db.idx.fulltext.queryNodes('i', $q)",
        "MATCH (e) RETURN size((e)-->())",
    )
    for text in corpus:
        ours = tool._NO_WS.sub("", tool._strip_noise(text))
        theirs = tool._NO_WS.sub("", gate.sub("", text))
        assert ours == theirs, (
            f"the guard's grammar drifts from the write gate's on {text!r}:\n"
            f"  graphevent_home: {ours!r}\n"
            f"  projection gate: {theirs!r}\n"
            "The two must agree wherever no backtick is involved; do not 'fix' this"
            " by deleting the copy without also giving the guard a backtick branch.")

    # The delta itself: a `//` inside a backticked identifier. The gate's grammar
    # has no backtick branch, so its `//` eats the pattern; the guard's unwraps the
    # identifier and still sees `-[`.
    delta = "MATCH (n) WHERE n.`a//b` = 1 MATCH (e)-[r:T]-(p)"
    ours = tool._NO_WS.sub("", tool._strip_noise(delta))
    theirs = tool._NO_WS.sub("", gate.sub("", delta))
    assert ours != theirs, (
        "the backtick branch is gone — without it a `//` inside a backticked"
        " identifier opens a line comment and a capability pattern after it"
        f" becomes invisible (both sides now {ours!r})")
    assert "-[" in ours and "-[" not in theirs
