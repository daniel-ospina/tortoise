"""#11641 — the two writers whose vector label was decided at RUNTIME now BOUND it.

`#11492` gave `#5090` DoD row 2 a declared `stores` per writer, and exactly two writers could not
answer WHERE: they store a vector for real, but the label came from outside the function — the
dump's own labels for `tortoise.hosted_backup::restore_graph`, the caller's statement for
`tortoise.sdk::_event_raw_write_door`. Declared `LABELS_AT_RUNTIME`, they held the row at
`PARTIAL`, because a runtime label cannot be compared to `run_vector_query`'s served set.

This suite pins the ANSWER, and it pins it in BOTH directions, because each half fails silently on
its own:

* the **bound** — a vector store onto a label the read path does not serve is REFUSED at the seam
  (`restore_graph` pre-mutation, so a refusal cannot leave a half-restored graph; the door before
  the journal append, so a refusal cannot leave a record with no node);
* the **legitimate path** — a served-label vector still restores, still lands as `vecf32`, and a
  door-written one is still reachable through `run_vector_query` (asserted by ID, not by type —
  type is not reachability, and for a door-written vector it can be false); a real `:Event` write
  still goes through the door.

RED proof (each assertion fails on the pre-fix tree):

* `test_restore_graph_refuses_an_unserved_label` — before the fix the restore silently wrote a
  vector onto `:Batch` (a label nothing queries) and returned counts as if all was well;
* `test_the_door_refuses_a_statement_outside_its_label` — before the fix the door executed any
  caller statement, and the register's own reason says the label was "not the door's to name";
* `test_the_door_refuses_a_statement_that_WRITES_TO_an_unproved_target` — before the write-target
  reader the door proved only the labels a statement NAMES, so `MATCH (e:Event …) CREATE (p) SET
  p += $props, <embedding on p>` passed and landed a `Vectorf32` on the label-less node `[]`;
* `test_the_door_refuses_a_BACKTICK_QUOTED_write_target` — a backtick-quoted alias (`` `b` ``) is an
  IDENTIFIER to the engine, not a string, and every reader in the seam used to match only the bare
  spelling: the door accepted `` … (`b`:Batch …) SET e += $props, `b`.embedding = … `` and stored the
  vector on `:Batch`;
* `test_a_CYPHER_COMMENT_cannot_hide_a_write_target` — a `)` inside `/* … */` drove the depth scan
  negative and deleted every following clause from the reader, so a `CREATE (p) SET
  p.embedding = …` behind a comment was invisible and accepted;
* `test_the_declared_restore_labels_are_the_served_vocabulary` — before the fix `restore_graph`
  declared `LABELS_AT_RUNTIME`, so row 2 had no label to compare and read `PARTIAL`.
"""
from __future__ import annotations

import os
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embed_writer_register import (
    EMBED_WRITER_CLASSES,
    LABELS_AT_RUNTIME,
)
from tortoise.hosted_backup import (
    _VECTOR_RESTORE_LABEL_SET,
    _VECTOR_RESTORE_LABELS,
    DUMP_FORMAT,
    restore_graph,
)
from tortoise.projection import FalkorProjection
from tortoise.sdk import _RAW_WRITE_DOOR_LABELS, TortoiseSDK
from tortoise.search_engine import run_vector_query
from tortoise.vector_write import (
    clear_then_set_prefix,
    served_store_labels,
    set_embedding_clause,
    statement_conflicting_bindings,
    statement_interpolates_label,
    statement_label_mutations,
    statement_node_labels,
    statement_pattern_labels,
    statement_rebound_aliases,
    statement_unattributed_set_items,
    statement_uses_unmodelled_scope,
    statement_write_aliases,
    statement_write_target_problem,
)

RESTORE_WRITER = "tortoise.hosted_backup::restore_graph"
DOOR_WRITER = "tortoise.sdk::_event_raw_write_door"

#: A vector of the width the store's index is created with, small enough to inline in a dump.
VEC = [0.01 * i for i in range(384)]


# ── the declarations, in BOTH directions ────────────────────────────────────────────────────────

def test_the_declared_restore_labels_are_the_served_vocabulary() -> None:
    """The restore's bound and the query path's vocabulary must be the SAME set — either way.

    ⛔ Both directions, because they fail differently and neither is loud:
    * a label ADDED to `tortoise.security.VALID_ENTITY_TYPES` makes the query path serve a label
      this bound still refuses — the restore would refuse a dump it should accept, so the
      declaration is now narrower than the product;
    * a label DROPPED from it leaves this tuple naming a label no read reaches — the register's
      declaration would then be the only thing in the product still asserting a query path that
      does not exist.
    """
    served = served_store_labels()
    assert set(_VECTOR_RESTORE_LABEL_SET) == served, (
        "`tortoise.hosted_backup::_VECTOR_RESTORE_LABELS` and the query path's served labels have "
        f"diverged: restore={sorted(_VECTOR_RESTORE_LABEL_SET)} served={sorted(served)}. The "
        "restore's bound is a MIRROR of the read path's vocabulary and must move with it"
    )
    assert {label for label, _prop in _VECTOR_RESTORE_LABELS} == served


def test_the_register_declares_the_sets_the_two_seam_constants_name() -> None:
    """The CONSTANTS and the DECLARATION name the same labels, and all of them are served.

    ⚠️ This is a COUPLING pin, not an enforcement pin, and it is named for what it does (#11641
    review): it compares `stores` ↔ `_VECTOR_RESTORE_LABELS`/`_RAW_WRITE_DOOR_LABELS` ↔
    `served_store_labels()`, and it passes with both writers' guards deleted. It exists so the
    declaration cannot drift from the constant a writer reads — that drift is how a declaration
    becomes the fabrication `LABELS_AT_RUNTIME` was invented to refuse. The REFUSALS are pinned in
    `test_the_door_refuses_*`, `test_restore_graph_refuses_an_unserved_label` and
    `test_row_2_reads_met_with_no_runtime_label_left`'s source-reading assertions; do not read this
    one as proof of enforcement.
    """
    assert EMBED_WRITER_CLASSES[RESTORE_WRITER].stores != LABELS_AT_RUNTIME, (
        "restore_graph is back to a runtime label — the restore must bound what it stores on, or "
        "row 2 cannot compare it to the served set"
    )
    assert set(EMBED_WRITER_CLASSES[RESTORE_WRITER].stores) == set(_VECTOR_RESTORE_LABEL_SET)
    assert EMBED_WRITER_CLASSES[DOOR_WRITER].stores == ("Event",)
    assert tuple(label for label, _prop in _RAW_WRITE_DOOR_LABELS) == (
        EMBED_WRITER_CLASSES[DOOR_WRITER].stores)
    # And every label EITHER writer declares must be one the read path serves: a declaration is
    # only green because it names served labels, so a served-set shrink must red here too.
    declared = (set(EMBED_WRITER_CLASSES[RESTORE_WRITER].stores)
                | set(EMBED_WRITER_CLASSES[DOOR_WRITER].stores))
    assert declared <= served_store_labels(), (
        f"{sorted(declared - served_store_labels())} is declared but no read path serves it"
    )


def test_row_2_reads_met_with_no_runtime_label_left() -> None:
    """The issue's target, measured through the instrument rather than asserted about the register.

    Reads the probe's own row-2 verdict, so it reds when a writer is declared labels-at-runtime or
    when a declared label leaves the served set.

    ⚠️ The verdict alone is NOT guard-sensitive: `scored_labels()` derives the row from the
    register's DECLARATION, so declaration-only assertions here pass with the writers' guards
    deleted (measured, and named rather than hidden). The three source-reading assertions at the end
    close that: two pin the DOOR's bound (present, and ordering it before the journal append) and
    one pins RESTORE_GRAPH's (present, and ordering it before the first node CREATE) — so deleting
    either writer's guard reds here too, instead of only in the sibling refusal tests.
    """
    from tools.dod_5090_probe import scored_labels, served_labels

    scored = scored_labels()
    assert not scored["unresolved"], (
        "row 2's HOLD set is non-empty — a writer's label is still not a label: "
        f"{sorted(scored['unresolved'])}"
    )
    assert scored["labels"] <= served_labels(), (
        f"row 2 would print an orphan: {sorted(scored['labels'] - served_labels())}"
    )
    # No separate `LABELS_AT_RUNTIME not in any entry` assertion: `scored_labels()` folds every
    # at-runtime writer into `unresolved`, so the assertion above already reds on one — and a
    # second, blanket pin would forbid the disposition outright, contradicting
    # `tortoise/embed_writer_register`'s own (correct) statement that it is a legal SHAPE for a
    # writer that genuinely has no bound (#11641 review). These two writers are pinned by name in
    # `test_the_two_former_runtime_label_writers_now_own_their_label`.

    # ── the guard-sensitivity the verdict above does not have ──────────────────────────────────
    # ENFORCEMENT, not declaration: the bound must EXIST in the writer, and it must run BEFORE the
    # write it protects (a guard after the CREATE would refuse a dump that had already mutated the
    # target — the #3895 discipline is about WHERE it runs, not only that it runs).
    import inspect

    from tortoise.hosted_backup import restore_graph as _restore
    from tortoise.sdk import TortoiseSDK as _SDK

    restore_src = inspect.getsource(_restore)
    guard_at = restore_src.index("_require_served_restore_labels(")
    first_write_at = restore_src.index("CREATE (n:")
    assert guard_at < first_write_at, (
        "restore_graph calls the vector-label guard AFTER creating nodes — a refusal would then "
        "leave the target partially mutated, which is the whole reason #3895 put its check in the "
        "pre-mutation pass"
    )

    door_src = inspect.getsource(_SDK._event_raw_write_door)
    assert "statement_write_target_problem(statement, _declared_labels)" in door_src, (
        "the door no longer reads the WRITE TARGET — the label-only rule accepts a statement that "
        "names :Event and stores on an unlabelled alias (measured), so row 2 would read MET over a "
        "vector no read path reaches"
    )
    assert door_src.index("_declared_labels = ") < door_src.index("_journal_event_raw_write("), (
        "the door's label refusal runs AFTER the journal append — a refused statement would leave "
        "a record for a node it never wrote, the live/replay divergence its own contract forbids"
    )


# ── the NAMING readers read text, and a text reader has to know what TEXT is ────────────────
#
# #11641's second review round found the naming legs quote-BLIND while the write-target leg was
# quote-aware: `SET e.note = '(b:Batch)'` made `statement_node_labels` return `{'Batch', 'Event'}`,
# so the door refused a legitimate statement and named a label the statement never binds. Making
# the readers quote-aware is a change in their ANSWER, so it is pinned in BOTH directions here —
# a fix whose whole subject is "the reader said something different" with no pin is a change that
# can be reverted silently.

@pytest.mark.parametrize("statement, expected", [
    # a STRING literal is not a pattern: the label inside it is text
    ("MATCH (e:Event {eventId:$eid}) SET e.note = '(b:Batch)'", {"Event"}),
    # …and it is not an interpolation either (this string made `statement_interpolates_label` True)
    ("MATCH (e:Event {eventId:$eid}) SET e.note = '(n:{x})'", {"Event"}),
    # a label in a COMMENT is not a label the statement binds
    ("MATCH (e:Event {eventId:$eid}) // (b:Batch)\nSET e += $props", {"Event"}),
    # a BACKTICK is Cypher's spelling of an IDENTIFIER, not a string: it must keep reading
    ("MATCH (`b`:Event {eventId:$eid}) SET b += $props", {"Event"}),
    # ⛔ and a quote INSIDE a backticked identifier must not open a phantom string (measured at
    # `66da0c05`: it blanked the rest of the statement, so the door refused a legal write AND a
    # stray `:Secret` pattern placed after it became invisible)
    ("MATCH (`a'b`:Event {eventId:$eid}) SET `a'b`.embedding = vecf32($embedding)", {"Event"}),
    ("MATCH (e:Event {eventId:$eid}), (`x'y`:Event {id:$z}), (s:Secret) "
     "SET e.embedding = vecf32($embedding)", {"Event", "Secret"}),
])
def test_the_naming_readers_know_what_text_is(statement: str, expected: set) -> None:
    assert statement_node_labels(statement) == expected, statement_node_labels(statement)
    if "(n:{x})" in statement and "'(n:{x})'" in statement:
        assert statement_interpolates_label(statement) is False, (
            "an interpolation spelled inside a STRING literal is not an interpolation"
        )


@pytest.mark.parametrize("statement", [
    # BOTH spellings of a rendered label. The `$` form is live in this tree
    # (`tortoise/cache_projection.py` builds `MATCH (n:$label …)`), and a reader that matched only
    # `{` answered False for it while its docstring claimed to catch it "on its own".
    "MATCH (e:Event {eventId:$eid}), (n:{kind}) SET n += $props",
    "MATCH (e:Event {eventId:$eid}), (n:$label {id:$rid}) SET n += $props",
    "MATCH (e:Event {eventId:$eid}), (n:$(label)) SET n += $props",
])
def test_every_rendered_label_spelling_is_refused(statement: str) -> None:
    assert statement_interpolates_label(statement) is True, statement_interpolates_label(statement)
    with pytest.raises(ValueError, match="INTERPOLATION"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={}, embedding=VEC, lane="test")


def test_a_terminal_RETURN_alias_is_not_a_rebind() -> None:
    """A `RETURN` ends its query part, so nothing after it can write — it cannot re-point a store.

    The first `statement_rebound_aliases` read every `AS` regardless of clause, so it refused
    `… SET e.embedding = … RETURN e.eventId AS e` — a statement the engine ACCEPTS — with a message
    asserting a rebind that had not happened. A false diagnosis of a legitimate statement is the one
    error class this seam's docstrings promise it does not produce.
    """
    valid = "MATCH (e:Event {eventId:'v'}) SET e.note = 'x' RETURN e.eventId AS e"
    assert statement_rebound_aliases(valid) == frozenset(), statement_rebound_aliases(valid)
    assert statement_write_target_problem(valid, {"Event"}) is None
    with pytest.raises(AssertionError, match="journal was reached"):   # accepted → reaches the journal
        DOOR(_NoJournal(), None, valid, event_id="e1", props={}, embedding=VEC, lane="test")
    # …but a re-point BEFORE the store is still refused.
    assert statement_rebound_aliases("MATCH (e:Event {id:$eid}) WITH 1 AS e SET e += $props") == {"e"}


def test_a_rebound_alias_is_refused_because_the_reader_unions_by_name() -> None:
    """A fail-OPEN that lived INSIDE the grammar, found at `66da0c05` — now DEFENCE IN DEPTH.

    `WITH b AS e` re-points `e` at a different node, so the label a pattern proved for `e` no longer
    describes what the store touches — and because the readers union bindings per alias NAME, all
    three legs passed while the vector landed on the LABEL-LESS node. Refused rather than reasoned
    about: proving which binding is live at the store needs real scope analysis.

    ⛔ WHAT CHANGED, and why this test asserts TWO different refusals rather than one. The reader
    below still answers `REBINDS` (kept — it is correct over what it still sees), but the DOOR no
    longer reaches it for this statement: `WITH` is outside the door's DECLARED GRAMMAR, so the
    grammar guard refuses the statement FIRST, before the target reader runs. That ordering is
    asserted rather than assumed — the door's message is the SCOPE one, and asking for `REBINDS`
    there would pin an ordering that no longer exists.
    """
    rebound = ("MATCH (e:Event {eventId:$eid}) WITH e AS f MATCH (b) WHERE b.id = $eid "
               "WITH b AS e SET e.embedding = vecf32($embedding)")
    problem = statement_write_target_problem(rebound, {"Event"})
    assert problem is not None and "REBINDS" in problem, problem
    # …and the door refuses it BEFORE the journal (the fake proves the ordering) — as a `WITH`,
    # because the grammar leg runs first and the rebind reader is behind it.
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, rebound, event_id="e1", props={}, embedding=VEC, lane="test")
    assert "WITH" in str(refused.value) and "scope" in str(refused.value), str(refused.value)


# ⛔ INVERTED, DELIBERATELY, AND HERE IS WHY (#11641 r5's accepted decision).
#
# At `556284de` these two statements were pinned as ACCEPTED — a projection that does not RE-POINT
# the alias is not a rebind, so the reader passed them and the door let them through. That pin is
# now WRONG, and it is not deleted quietly: under the declared grammar a `WITH` is REFUSED, so both
# statements are rejected by the door. The reason is the one the whole refusal exists for — a
# name-keyed reader cannot prove what a name means across a scope boundary, and the round-5
# reproduction (`WITH 1 AS x` then `MATCH (p)`) is exactly this shape with the alias dropped instead
# of carried. Pricing that soundness at these two statements is the trade the decision accepted: a
# grammar that cannot be defeated by the next parse shape, instead of a scope model that can.
#
# The reader's own answer is pinned BESIDE the door's, because the two now disagree ON PURPOSE —
# `statement_write_target_problem` still returns `None` for both (it is scope-blind by design and
# reachable for non-`WITH` shapes), while the door refuses them. A future edit that removes the
# grammar leg should red this test, not silently restore the old acceptance.
@pytest.mark.parametrize("statement", [
    "MATCH (e:Event {eventId:$eid}) WITH e, e.eventId AS eid2 "
    + "SET e += $props, " + set_embedding_clause("e"),
    "MATCH (e:Event {eventId:$eid}) WITH e WHERE e.eventId = $eid "
    + "SET e += $props, " + set_embedding_clause("e"),
])
def test_a_NON_rebinding_projection_is_now_REFUSED_by_the_declared_grammar(statement: str) -> None:
    """WAS `statement_write_target_problem(…) is None` AS ACCEPTANCE at `556284de` — INVERTED.

    See the comment block above: the reader still passes these (scope-blind), and that is pinned so
    the two answers cannot drift apart silently; the DOOR refuses them, and that is the behaviour
    this product now has.
    """
    assert statement_write_target_problem(statement, {"Event"}) is None, (
        "the write-target reader now refuses a non-rebinding projection on its own — if that is "
        f"deliberate, this inversion's premise needs re-deriving: {statement}"
    )
    assert statement_uses_unmodelled_scope(statement) == "WITH", (
        statement_uses_unmodelled_scope(statement))
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={}, embedding=VEC, lane="test")
    message = str(refused.value)
    assert "WITH" in message and "DECLARED grammar" in message, message
    assert message.rstrip().endswith("(#11641)."), message


def test_a_store_inside_a_property_map_is_refused() -> None:
    """The THIRD store shape, and the one no alias-based reader can follow (#11641 review 3).

    Both of these were ACCEPTED by all three legs and both landed a `Vectorf32` where no read path
    reaches: a map on an anonymous pattern writes to a node with NO label at all, and a map on a
    relationship writes a property `run_vector_query` never looks at.
    """
    for statement in (
        # anonymous node, vector in the map
        "MATCH (e:Event {eventId:$eid}) CREATE ({embedding: vecf32($embedding)}) SET e += $props",
        # relationship property
        "MATCH (e:Event {eventId:$eid}) "
        "CREATE (e)-[r:R {embedding: vecf32($embedding)}]->() SET e += $props",
        # named but LABEL-LESS node, vector in the map
        "MATCH (e:Event {eventId:$eid}) CREATE (q {embedding: vecf32($embedding)}) SET e += $props",
    ):
        problem = statement_write_target_problem(statement, {"Event"})
        assert problem is not None and "property MAP" in problem, problem
        with pytest.raises(ValueError, match="property MAP"):
            DOOR(_NoJournal(), None, statement, event_id="e1", props={},
                 embedding=VEC, lane="test")
    # …and the accept direction: an ordinary property map without the vector is fine (every one of
    # the six real doors carries `{eventId:$eid}`).
    ok = ("CREATE (e:Event {eventId: $eid}) SET e += $props, "
          + set_embedding_clause("e", preserve_on_null=False))
    assert statement_write_target_problem(ok, {"Event"}) is None
    # …and a map inside a STRING is text, not a store.
    assert statement_write_target_problem(
        "MATCH (e:Event {eventId:$eid}) SET e.note = '{embedding: 1}'", {"Event"}) is None


def test_one_alias_bound_to_differing_label_sets_is_refused() -> None:
    """`UNION` arms bind the same NAME to DIFFERENT nodes, so the union of labels is a lie.

    `statement_pattern_labels` unions per alias NAME — correct for a repeated alias inside one
    `MATCH`, wrong across arms: the arm that binds `e` with no label contributes nothing to the
    union, so the reader still proves `{Event}` while that arm stores on a label-less node. Measured
    through the door on a seeded graph at `556284de`, where a `:Batch` and a `:Session` both came
    out holding a `Vectorf32`.
    """
    union = ("MATCH (e:Event {eventId:$eid}) SET e.embedding = vecf32($embedding) RETURN 1 "
             "UNION MATCH (e) SET e.embedding = vecf32($embedding) RETURN 1")
    problem = statement_write_target_problem(union, {"Event"})
    assert problem is not None and "DIFFERING label sets" in problem, problem
    # The DOOR refuses it too — with the GRAMMAR guard's message, which is the earlier of the two
    # refusals (`UNION` is a scope construct). Both are correct; the door's own ordering decides
    # which one a caller sees, so only "it is refused, and for a named reason" is pinned here.
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, union, event_id="e1", props={}, embedding=VEC, lane="test")
    assert ("DIFFERING label sets" in str(refused.value)
            or "UNION" in str(refused.value)), str(refused.value)
    # Accept: the same alias bound twice with the SAME labels is one node, not a divergence.
    assert statement_write_target_problem(
        "MATCH (e:Event {eventId:$eid}) MATCH (e:Event) "
        + "SET e += $props, " + set_embedding_clause("e"), {"Event"}) is None
    # …and an alias bound once is not reported at all.
    assert statement_conflicting_bindings(
        "MATCH (e:Event {eventId:$eid}) SET e += $props") == {}


# ── the door: refused shapes, and the real shape that must keep working ────────────────────────

class _NoJournal:
    """A door receiver whose journal must NEVER be reached by a refused statement.

    The refusal has to come BEFORE the append: a door that journals first and refuses after would
    leave a record for a node it never wrote — the exact live/replay divergence the door's own
    docstring forbids. So the fake RAISES if the append is touched at all.
    """

    def _journal_event_raw_write(self, *args, **kwargs):  # pragma: no cover - asserted not called
        raise AssertionError(
            "the journal was reached for a REFUSED statement — the door's bound must run first"
        )


DOOR = TortoiseSDK._event_raw_write_door


@pytest.mark.parametrize("statement", [
    "MATCH (b:Batch {id:$eid}) SET b += $props",
    "CREATE (b:Batch {id:$eid}) SET b += $props",
    "MATCH (e:Event {eventId:$eid}), (b:Batch {id:$eid}) SET b += $props",
    "MATCH (e {eventId:$eid}) SET e += $props",
    # ⛔ THE WRITE-TARGET ESCAPES (#11641 review). Each NAMES a declared label, so the label-only
    # half of the bound passed them — measured: the first one was accepted and the `:Batch` node
    # came out holding a `Vectorf32`, i.e. a vector paid for that no read path reaches, which is
    # the false pass this issue exists to close. The bound must read where the store LANDS.
    "MATCH (e:Event {eventId:$eid}) MATCH (b {id:'b1'}) SET b.embedding = vecf32($embedding)",
    "MATCH (e:Event {eventId:$eid}) MATCH (b) SET b.embedding = $embedding",
    "MATCH (e:Event {eventId:$eid}) MATCH (n:{lbl}) SET n.embedding = $embedding",
    "MATCH (e:Event {eventId:$eid}) SET e += $props, n.embedding = $embedding",
    "MATCH (e:Event {eventId:$eid}), (b) " + set_embedding_clause("b"),
])
def test_the_door_refuses_a_statement_outside_its_label(statement: str) -> None:
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "_event_raw_write_door" in str(refused.value), (
        "the refusal must name the writer a reader can go look at"
    )
    assert ("no read path" in str(refused.value)
            or "door stores on" in str(refused.value)
            or "NO node label" in str(refused.value)
            or "STORES a vector through alias" in str(refused.value)
            or "WRITE TARGET" in str(refused.value)
            or "write target" in str(refused.value)
            or "INTERPOLATION" in str(refused.value)
            or "alias(es)" in str(refused.value)), str(refused.value)


@pytest.mark.parametrize("statement", [
    # The four literal statements `tortoise/sdk.py::ingest_corpus` passes, built with the real
    # helpers so the shape is the product's, not a paraphrase of it.
    "MATCH (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "CREATE (e:Event {eventId:$eid}) SET e += $props",
    "MATCH (e:Event {eventId:$eid}) SET e += $props",
    # …and the two `tortoise/session_indexer.py::main` builds.
    "MATCH (e:Event {eventId: $eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "CREATE (e:Event {eventId: $eid}) SET e += $props, "
    + set_embedding_clause("e", preserve_on_null=False),
])
def test_every_real_door_shape_is_inside_the_bound(statement: str) -> None:
    """The accept direction: the bound must not refuse the product's own six statements."""
    assert statement_node_labels(statement) == {"Event"}, statement
    # …and the WRITE-TARGET reader is satisfied too — the check the door now runs, asserted on the
    # real shapes rather than only on the synthetic ones. A reader that refused here would break
    # ingestion, so this is the anti-vacuity half of the refuse rows below.
    assert statement_write_target_problem(statement, {"Event"}) is None, (
        statement_write_target_problem(statement, {"Event"})
    )
    assert statement_write_aliases(statement) == {"e"}, statement_write_aliases(statement)
    assert statement_label_mutations(statement) == frozenset(), statement
    # …and the bound itself accepts them (past the raise, onto the journal refusal — which is what
    # proves the guard let them through).
    with pytest.raises(AssertionError, match="journal was reached"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")


def test_the_door_refuses_a_statement_that_INTERPOLATES_a_label() -> None:
    """The refused shape a reader of LITERAL labels cannot see (#11641).

    `statement_node_labels` returns only the labels a statement NAMES, so a caller can satisfy the
    bound with one literal `(e:Event …)` pattern and store the vector on a SECOND pattern whose
    label it renders at runtime: `MATCH (e:Event {eventId:$eid}), (n:{kind}) SET n.embedding = …`.
    The bound would read `{'Event'}`, pass, and the vector would land wherever `kind` points — the
    label inherited from the caller again, which is the whole defect `#11641` closes.

    ⛔ NON-VACUITY: the STATEMENT is the proof of this assertion's reach — the reader below really
    does see nothing wrong with it (`{'Event'}`, no stray label), so on a bound that only reads
    named labels the door reaches the JOURNAL (the fake raises), which is exactly what the pre-fix
    tree does for every statement.
    """
    statement = (
        "MATCH (e:Event {eventId:$eid}), (n:{kind}) "
        + clear_then_set_prefix("n") + "SET n += $props, " + set_embedding_clause("n")
    )
    # The reader's blind spot, asserted rather than assumed: the literal label is all it sees.
    assert statement_node_labels(statement) == {"Event"}, statement_node_labels(statement)
    assert statement_interpolates_label(statement) is True
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "INTERPOLATION" in str(refused.value), str(refused.value)
    assert "_event_raw_write_door" in str(refused.value), (
        "the refusal must name the writer a reader can go look at"
    )


def test_no_real_door_shape_is_mistaken_for_an_interpolation() -> None:
    """The other direction: the product's own statements must not trip the new refusal.

    A guard that refused a real shape would break ingestion, and a nested map literal
    (`{meta: {a: 1}}`) is the ONE text shape a loose `:{` pattern would misread — so both sides of
    the boundary are pinned here rather than reasoned about.
    """
    for statement in (
        "MATCH (e:Event {eventId:$eid}) SET e += $props",
        "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
        + "SET e += $props, " + set_embedding_clause("e"),
        # A nested map literal is NOT a label interpolation — the pattern is anchored to the node
        # pattern's `(alias:` prefix for exactly this reason.
        "MATCH (e:Event {eventId:$eid}) SET e.meta = {a: {b: 1}}",
    ):
        assert statement_interpolates_label(statement) is False, statement


def test_a_real_event_write_still_lands_on_event(tmp_path) -> None:
    """End to end: the door's bound does not break the writer it guards (live, not simulated).

    ⛔ The last assertion is a READ-BACK, not a type check: `typeof(n.embedding) == 'Vectorf32'`
    says a vector is stored, never that the query leg can reach it — and for a door-written vector
    reachability is exactly what the `#5090` row-2 defect falsifies. So the node must come back out
    of `run_vector_query(entity_type="event")`, by ID.
    """
    sdk = TortoiseSDK(str(tmp_path / f"{uuid4().hex}.db"))
    try:
        proj = sdk._get_proj()
        written = sdk._event_raw_write_door(
            proj,
            "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
            + "SET e += $props, " + set_embedding_clause("e"),
            event_id="evt-11641",
            props={"eventId": "evt-11641", "eventKind": "DocumentCreated"},
            event_kind="DocumentCreated",
            embedding=VEC,
            lane="test-11641",
        )
        assert written is True
        rows = proj.g.query(
            "MATCH (e:Event {eventId:'evt-11641'}) RETURN typeof(e.embedding)"
        ).result_set
        assert rows and rows[0][0] == "Vectorf32", rows
        # …and it is REACHABLE through the query leg's own door (#5090 row 2's question).
        hits = run_vector_query(proj.g, VEC, limit=10, is_embedded=True,
                                entity_type="event", excluded_statuses=())
        assert "evt-11641" in [hit[0] for hit in hits], (
            "the door wrote a vector the query leg cannot reach — a stored vector with no path to "
            f"query it is the exact defect row 2 is scored on: {hits}"
        )
    finally:
        sdk.close()


# ── the door: the WRITE TARGET, not only the named labels (#11641 P1) ─────────────────────────

@pytest.mark.parametrize("statement, named, problem_fragment, door_fragment", [
    # The review's reproduction: a served label is NAMED in the MATCH, and the vector is stored on
    # a `CREATE`d alias that carries no label at all. `statement_node_labels` returns `{'Event'}`, so
    # the old bound passed this and the vector landed on the label-less node `[]`.
    ("MATCH (e:Event {eventId:'seed'}) CREATE (p) SET p += $props, "
     + set_embedding_clause("p"), {"Event"}, "NO LABEL", "WRITE TARGET"),
    # The same via a relationship: the alias is still bound with no label.
    ("MATCH (e:Event {eventId:'seed'}) CREATE (e)-[:R]->(p) SET p += $props, "
     + set_embedding_clause("p"), {"Event"}, "NO LABEL", "WRITE TARGET"),
    # An unlabelled MERGE target.
    ("MATCH (e:Event {eventId:'seed'}) MERGE (p) SET p += $props, "
     + set_embedding_clause("p"), {"Event"}, "NO LABEL", "WRITE TARGET"),
    # LABEL MUTATION: the alias IS bound to a served label, and the statement then removes it and
    # attaches another one — so the label at the moment the vector lands is not the one the pattern
    # reader saw. Nothing but a mutation check reaches this.
    ("MATCH (e:Event {eventId:'seed'}) REMOVE e:Event SET e:Foo, "
     + set_embedding_clause("e"), {"Event"}, "MUTATES", "WRITE TARGET"),
    # A vector store on an alias bound to a label OUTSIDE the door's set. The named-label bound
    # refuses this shape too — the row pins that the WRITE-TARGET reader independently reports the
    # alias, which is the reader the other four rows depend on.
    ("MATCH (p:Batch {id:$eid}) SET p.embedding = $embedding", {"Batch"},
     "outside the allowed set", "names label"),
])
def test_the_door_refuses_a_statement_that_WRITES_TO_an_unproved_target(
        statement: str, named: set[str], problem_fragment: str, door_fragment: str) -> None:
    """The refused shapes a reader of NAMED labels cannot see (P1, the review's reproduction).

    `statement_node_labels` returns the labels a statement MENTIONS. FOUR of these rows pass that
    bound while the vector lands somewhere else — on a label-less node (reachable by no
    `entity_type`), or on a served label the statement itself then MUTATES away. The FIFTH row
    (`:Batch`, outside the door's set) is refused by the named-label bound as well, and is here to
    pin that the write-target reader independently reports it too: if only one of the two readers
    saw it, the other would be silently narrower than it claims.

    ⛔ NON-VACUITY, asserted first: `statement_node_labels(statement) == named` proves the
    named-label bound had a label to read, and `statement_write_target_problem(...)` being non-None
    proves the WRITE TARGET is what that reader recognises — including on the fifth row, where the
    two readers overlap by design. On the pre-fix tree the door therefore reached the JOURNAL (the
    fake raises), which is the RED direction for every row.
    """
    assert statement_node_labels(statement) == named, statement_node_labels(statement)
    problem = statement_write_target_problem(statement, {"Event"})
    assert problem is not None, (
        f"the write-target reader did NOT see this store: {statement}"
    )
    assert problem_fragment in problem, problem
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "_event_raw_write_door" in str(refused.value), (
        "the refusal must name the writer a reader can go look at"
    )
    assert door_fragment in str(refused.value), str(refused.value)


# ── the door: the GRAMMAR it accepts, and every scope construct it refuses (#11641, r5) ────────
#
# The readers above key an alias's labels BY NAME, over the whole statement, with no scope boundary.
# Cypher's `WITH` ends exactly that: a variable the clause does not carry "is dropped from the scope
# of the query and cannot be referenced by subsequent clauses" (Neo4j Cypher Manual, `WITH` →
# *Control variables in scope*), so a later `MATCH (p)` binds a NEW, unlabelled `p` while a
# name-keyed map still reports the served label the FIRST pattern gave it. Modelling scopes is a
# parser, and an under-modelled parser fails OPEN — so the door DECLARES the grammar it accepts
# (`MATCH` / `MERGE` / `CREATE` / `SET` / `REMOVE` / `FOREACH`) and refuses everything outside it.
# These rows are that declaration's teeth.

#: ``(id, statement, construct)`` — one row per construct the door must refuse. The construct string
#: is asserted against BOTH the reader and the door's message, so a row cannot pass by refusing for
#: an unrelated reason.
SCOPE_REFUSE_ROWS: list[tuple[str, str, str]] = [
    (
        # ⛔ THE FIFTH ROUND'S REPRODUCTION, VERBATIM. A served alias bound BEFORE a `WITH` that does
        # not carry it, then re-bound by NAME in the fresh scope: `WITH 1 AS x` drops `p`, so the
        # second `MATCH (p)` matches EVERY node and the vector lands on all of them. Measured at
        # `1f82113d`: ACCEPTED, `VectorF32` on `:Meta` — a label no `entity_type` reaches.
        "WITH re-binding the same alias",
        "MATCH (p:Event {eventId:'seed'}) WITH 1 AS x MATCH (p) SET "
        + set_embedding_clause("p"),
        "WITH",
    ),
    (
        "UNION",
        "MATCH (p:Event {eventId:'seed'}) SET " + set_embedding_clause("p")
        + " RETURN p UNION MATCH (p) SET " + set_embedding_clause("p"),
        "UNION",
    ),
    (
        "CALL { … } subquery",
        "CALL { MATCH (p) SET " + set_embedding_clause("p") + " } RETURN 1",
        "CALL {",
    ),
    (
        "OPTIONAL MATCH",
        "MATCH (p:Event {eventId:'seed'}) SET " + set_embedding_clause("p")
        + " OPTIONAL MATCH (q) SET " + set_embedding_clause("q"),
        "OPTIONAL MATCH",
    ),
]


@pytest.mark.parametrize("label, statement, construct", SCOPE_REFUSE_ROWS,
                         ids=[row[0] for row in SCOPE_REFUSE_ROWS])
def test_the_door_refuses_a_scope_construct_it_cannot_prove(
        label: str, statement: str, construct: str) -> None:
    """Every declared scope construct: the READER fires, the DOOR raises, the JOURNAL is untouched.

    ⛔ The three assertions answer three different questions and none is decoration. The reader
    firing proves the grammar guard is not vacuous. The door raising proves the guard is WIRED IN
    (a reader nothing calls is documentation, not a bound). `_NoJournal` proves the refusal runs
    BEFORE the append — a door that journals first and refuses after leaves a record for a node it
    never wrote, which is the live/replay divergence its own contract forbids.
    """
    fired = statement_uses_unmodelled_scope(statement)
    assert fired is not None, (
        f"the grammar reader did NOT fire on a `{construct}` statement: {statement}"
    )
    assert construct in fired, (fired, construct, statement)
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    message = str(refused.value)
    assert "_event_raw_write_door" in message, (
        f"the refusal must name the writer a reader can go look at: {message}"
    )
    assert construct in message, (
        f"the refusal must name the construct it refused ({construct}): {message}"
    )
    assert "scope" in message, (
        "the refusal must say WHY — that an alias's binding cannot be proved ACROSS A SCOPE "
        f"BOUNDARY, so a served label bound before the boundary does not prove where the vector "
        f"lands: {message}"
    )
    assert message.rstrip().endswith("(#11641)."), (
        f"the refusal must end with its issue reference so a reader can trace it: {message}"
    )


def test_the_scope_blind_spot_is_exactly_what_the_name_keyed_readers_miss() -> None:
    """NON-VACUITY for the row above: the name-keyed readers are BLIND to the reproduction.

    ⛔ This is the whole reason the grammar guard exists rather than a stricter target reader. On
    `MATCH (p:Event {eventId:'seed'}) WITH 1 AS x MATCH (p) SET <embedding>` every reader this seam
    has reports a SERVED, LABELLED binding — measured at `1f82113d`, where the door accepted it and
    the vector landed on `:Meta`. So no amount of tightening the label/target/interpolation/~
    mutation readers closes this: only refusing the construct does. Asserted first, because a guard
    whose stated blind spot does not reproduce is a guard guarding nothing.
    """
    statement = ("MATCH (p:Event {eventId:'seed'}) WITH 1 AS x MATCH (p) SET "
                 + set_embedding_clause("p"))
    assert statement_pattern_labels(statement) == {"p": frozenset({"Event"})}, (
        statement_pattern_labels(statement))
    assert statement_node_labels(statement) == {"Event"}, statement_node_labels(statement)
    assert statement_write_aliases(statement) == {"p"}, statement_write_aliases(statement)
    assert statement_label_mutations(statement) == frozenset()
    assert statement_interpolates_label(statement) is False
    # ⚠️ RE-DERIVED (#11641, after the conflicting-bindings reader landed): these two assert the
    # reader's own verdict rather than "the target reader is blind". It is no longer blind — an
    # alias bound to DIFFERING label sets is refused by the target reader too, so the grammar guard
    # is now DEFENCE IN DEPTH rather than the sole defence. What stays true, and is what the guard
    # is for, is that it refuses the shape WITHOUT modelling scope at all: every name-keyed reader
    # above still reports a served, labelled binding, so the guard's protection does not depend on
    # any of them being right about scopes.
    assert statement_uses_unmodelled_scope(statement) == "WITH"
    problem = statement_write_target_problem(statement, {"Event"})
    assert problem is not None and "DIFFERING label sets" in problem, problem


@pytest.mark.parametrize("statement", [
    # The product's six door statements, built with the real helpers — the ACCEPT direction. A
    # grammar guard that refused one of these would break ingestion, so this is the anti-vacuity
    # half of every refuse row: the guard must be narrow enough to leave the product alone.
    "MATCH (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "MERGE (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "CREATE (e:Event {eventId:$eid}) SET e += $props",
    "MATCH (e:Event {eventId:$eid}) SET e += $props",
    "MATCH (e:Event {eventId: $eid}) " + clear_then_set_prefix("e")
    + "SET e += $props, " + set_embedding_clause("e"),
    "CREATE (e:Event {eventId: $eid}) SET e += $props, "
    + set_embedding_clause("e", preserve_on_null=False),
])
def test_the_grammar_guard_accepts_every_product_door_statement(statement: str) -> None:
    """The declared grammar is the product's OWN grammar — all six statements are inside it."""
    assert statement_uses_unmodelled_scope(statement) is None, (
        f"the grammar guard refuses a statement the product itself executes: "
        f"{statement_uses_unmodelled_scope(statement)} in {statement}"
    )
    # …and the door gets PAST the guard onto the journal refusal, which is what proves it let them
    # through (the fake raises only if the append is reached).
    with pytest.raises(AssertionError, match="journal was reached"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")


@pytest.mark.parametrize("statement", [
    # ⛔ ANTI-VACUITY IN THE OTHER DIRECTION, and the reason the guard reuses `_blank_comments` +
    # `_depths` instead of testing `"WITH" in statement`: a construct inside a STRING LITERAL is
    # text, not syntax. A substring guard would refuse every one of these while proving nothing.
    "MATCH (e:Event {eventId:$eid}) SET e += $props, e.note = 'WITH 1 AS x MATCH (p)'",
    "MATCH (e:Event {eventId:$eid}) SET e += $props, e.note = 'CALL { UNION }'",
    # …inside a LINE comment.
    "MATCH (e:Event {eventId:$eid}) SET e += $props // WITH 1 AS x",
    # …inside a BLOCK comment.
    "MATCH (e:Event {eventId:$eid}) /* WITH 1 AS x UNION CALL { } */ SET e += $props",
    # …and inside a BACKTICK-QUOTED identifier, which the engine lexes as an identifier, not a
    # string — see `_identifier_at`.
    "MATCH (`with`:Event {eventId:$eid}) SET `with` += $props",
])
def test_a_WITH_in_a_string_or_a_comment_is_NOT_a_scope_construct(statement: str) -> None:
    """A construct the ENGINE does not see as syntax must not be refused.

    The guard is a bound, not a search: refusing a `WITH` inside a string or a comment would block
    statements the engine accepts (the product writes user text into `e.note`) while proving
    nothing about scope.
    """
    assert statement_uses_unmodelled_scope(statement) is None, (
        f"a construct inside a string / comment / quoted identifier was read as SYNTAX: "
        f"{statement_uses_unmodelled_scope(statement)} in {statement}"
    )
    with pytest.raises(AssertionError, match="journal was reached"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")


@pytest.mark.parametrize("statement", [
    # A scope KEYWORD used as a plain VARIABLE: `MATCH (with)` binds an alias named `with`.
    "MATCH (with) SET with.embedding = $embedding",
    # A property named after one: `.call` is a property access, not a `CALL` clause.
    "MATCH (e:Event) SET e.call = 1",
    # `SET e.with = 1` — same, the keyword is a property NAME.
    "MATCH (e:Event) SET e.with = 1, e.embedding = $embedding",
])
def test_a_scope_KEYWORD_used_as_an_identifier_is_not_refused(statement: str) -> None:
    """Precision, in the false-BLOCK direction: the token match must not fire on a variable.

    `MATCH (with)` and `SET e.call = …` are statements the engine accepts. They are refused by the
    LABEL rules where appropriate (`(with)` binds no label), but they must not be refused as SCOPE
    constructs — a guard that fires on the substring would report the wrong reason and teach a
    caller to re-spell something that was never the problem.
    """
    assert statement_uses_unmodelled_scope(statement) is None, (
        f"the grammar guard read an IDENTIFIER as a scope construct: "
        f"{statement_uses_unmodelled_scope(statement)} in {statement}"
    )


@pytest.mark.parametrize("statement", [
    # A new binding introduced by a NON-scope clause, storing on the alias the pattern proved: the
    # grammar guard must read `None` and the door must let it through, because `UNWIND … AS x`
    # cannot RE-BIND a name that is already bound — it only introduces a new one, which cannot
    # defeat a name-keyed map.
    "MATCH (e:Event {eventId:$eid}) UNWIND [1] AS x SET e += $props, "
    + set_embedding_clause("e"),
    # A read-only clause after the store.
    "MATCH (e:Event {eventId:$eid}) SET e += $props, " + set_embedding_clause("e")
    + " RETURN e.eventId AS eid",
    # A read-only FILTER between the pattern and the store.
    "MATCH (e:Event {eventId:$eid}) WHERE e.eventId = $eid SET e += $props, "
    + set_embedding_clause("e"),
])
def test_a_NON_scope_clause_is_not_refused_by_the_grammar_guard(statement: str) -> None:
    """`None` means "no construct in the refusal LIST", not "every clause is allow-listed".

    ⛔ Pinned because the reader's docstring now says so, and an unpinned docstring claim is the
    drift this PR's own review rounds keep finding. The guard is a REFUSAL list, not an allow-list:
    `UNWIND`, `WHERE` and `RETURN` are accepted, and leaving them is safe for the two reasons the
    docstring names — either harmless (these three rows) or caught by the write-target leg (the row
    below).
    """
    assert statement_uses_unmodelled_scope(statement) is None, (
        f"a NON-scope clause was refused as a scope construct: "
        f"{statement_uses_unmodelled_scope(statement)} in {statement}"
    )


def test_a_store_on_an_UNWIND_binding_is_refused_by_the_WRITE_TARGET_leg() -> None:
    """The companion row: a NON-scope clause that introduces the STORE TARGET is still refused.

    `UNWIND $rows AS p` never binds `p` in a node PATTERN, so the write-target leg reads it as
    ABSENT — which is why leaving `UNWIND` out of the refusal list cannot be a hole. ⚠️ A served
    label is MATCHed beside it on purpose: with no label anywhere the statement is refused by the
    EARLIER `names NO node label` leg (`#11641`), and this row would then be pinning the wrong leg
    while reading as if it pinned the target one (measured — that is exactly what the first draft of
    this test did). A guard that grew `UNWIND` would refuse this for the wrong reason and teach a
    caller to re-spell a statement whose real problem is the target.
    """
    statement = ("MATCH (e:Event {eventId:$eid}) UNWIND $rows AS p SET "
                 + set_embedding_clause("p"))
    assert statement_uses_unmodelled_scope(statement) is None, (
        statement_uses_unmodelled_scope(statement))
    assert statement_node_labels(statement) == {"Event"}, statement_node_labels(statement)
    problem = statement_write_target_problem(statement, {"Event"})
    assert problem is not None and "NO node pattern" in problem, problem
    with pytest.raises(ValueError, match="WRITE TARGET"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={}, embedding=VEC, lane="test")


def test_the_remaining_scope_constructs_are_declared_and_refused() -> None:
    """The two constructs with no parametrized row: `LOAD CSV` and `IN TRANSACTIONS`.

    Both are in the declared list for the same reason as the four above, and both are pinned here
    because they need a shape the parameter rows do not carry. ⚠️ HONESTY ABOUT ORDER: a real
    `IN TRANSACTIONS` clause always follows a closed `CALL { … }`, and `CALL` comes FIRST in
    statement order — so on the realistic statement the door refuses it as a subquery. The list
    entry is therefore pinned on its own, on the smallest text that carries the token in clause
    position, so it cannot be dropped as "shadowed anyway" by a later edit.
    """
    load_csv = "LOAD CSV FROM 'file:///x.csv' AS row CREATE (e:Event {eventId:row.id})"
    assert statement_uses_unmodelled_scope(load_csv) == "LOAD CSV", (
        statement_uses_unmodelled_scope(load_csv))
    # A PROCEDURE call is the shape the residual used to hand to review: `CALL apoc.*` can mutate
    # the node the vector lands on, and it is refused as a construct now, not left to a reviewer.
    procedure = "MATCH (e:Event {eventId:$eid}) CALL apoc.mutate(e) RETURN 1"
    assert statement_uses_unmodelled_scope(procedure) == "CALL", (
        statement_uses_unmodelled_scope(procedure))
    for statement in (
        load_csv,
        procedure,
        # The realistic shape — refused, by its `CALL { … }` (first in statement order).
        "CALL { MATCH (e:Event) SET e.embedding = $embedding }"
        " IN TRANSACTIONS OF 100 ROWS",
    ):
        with pytest.raises(ValueError, match="scope"):
            DOOR(_NoJournal(), None, statement, event_id="e1", props={},
                 embedding=VEC, lane="test")
    # The `IN TRANSACTIONS` ENTRY on its own, with no `CALL` before it to shadow it. ⚠️ The
    # preceding `}` must be BALANCED: `_depths` goes negative on a stray closer — the same
    # mechanism that made a `)` inside a comment hide a whole clause — so an unbalanced text would
    # prove nothing about the reader. Valid Cypher cannot spell this at all (the token needs a
    # `CALL` subquery), so this is deliberately a TEXT-level pin of the list entry.
    assert statement_uses_unmodelled_scope(
        "MATCH (e:Event) SET e.probe = {} IN TRANSACTIONS OF 100 ROWS"
    ) == "IN TRANSACTIONS"
    # ⛔ …and the word `transactions` as a VARIABLE after `IN` is not the clause: no closing
    # subquery brace precedes it, so precision is kept where the token alone would misfire.
    assert statement_uses_unmodelled_scope(
        "MATCH (e:Event) WHERE e.id IN transactions RETURN e") is None


def test_a_property_removal_is_not_read_as_a_label_mutation() -> None:
    """⛔ `REMOVE e.embedding` is a PROPERTY removal, not a label mutation — it must stay accepted.

    `clear_then_set_prefix`/`clear_embedding` REMOVE the embedding property before rewriting it
    (the #4520 invariant). A mutation reader that keyed on the `REMOVE` keyword alone would refuse
    the product's own clear guard and break every overwrite — so the boundary is pinned here on the
    reader AND on the door.
    """
    statement = ("MATCH (e:Event {eventId:$eid}) " + clear_then_set_prefix("e")
                 + "SET e += $props, " + set_embedding_clause("e"))
    assert "REMOVE e.embedding" in statement, statement
    assert statement_label_mutations(statement) == frozenset(), statement_label_mutations(
        statement)
    assert statement_write_target_problem(statement, {"Event"}) is None, (
        statement_write_target_problem(statement, {"Event"})
    )
    with pytest.raises(AssertionError, match="journal was reached"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")


def test_the_target_readers_see_the_alias_a_pattern_binds_and_nothing_else() -> None:
    """The readers' shapes, pinned so a later simplification cannot quietly widen or narrow them.

    A naive `SET.*,` splitting regex is the obvious wrong implementation, and it is fooled by two
    shapes that occur in the product: a comma-separated MATCH pattern list, and a nested map
    literal in a `SET` item. Both are asserted here.
    """
    # Comma-separated MATCH patterns: `(b:Batch …)` is a SECOND pattern, not a second clause.
    statement = "MATCH (e:Event {eventId:$eid}), (b:Batch {id:$b}) SET e += $props"
    assert statement_pattern_labels(statement) == {
        "e": frozenset({"Event"}), "b": frozenset({"Batch"})}
    assert statement_write_aliases(statement) == {"e"}, statement_write_aliases(statement)

    # A nested map literal with commas is ONE `SET` item, and it writes to `e` only.
    statement = "MATCH (e:Event {eventId:$eid}) SET e.meta = {a: {b: 1}}, e += $props"
    assert statement_write_aliases(statement) == {"e"}, statement_write_aliases(statement)
    assert statement_write_target_problem(statement, {"Event"}) is None

    # An alias bound with NO label maps to the EMPTY frozenset — the absence the door refuses.
    assert statement_pattern_labels("MATCH (e:Event), (p) SET p += $props")["p"] == frozenset()

    # A label MUTATION is reported; a property removal is not.
    assert statement_label_mutations("MATCH (e:Event) REMOVE e:Event") == {"e"}
    assert statement_label_mutations("MATCH (e:Event) REMOVE e.embedding") == frozenset()
    assert statement_label_mutations("MATCH (e:Event) SET e:Foo") == {"e"}



# ── the door: an ACCEPTED identifier spelling the readers could not see (#11641, 4th round) ───

@pytest.mark.parametrize("statement, named, problem_fragment, door_fragment", [
    # The review's reproduction, verbatim. A served label is NAMED in the MATCH and the vector is
    # stored through a BACKTICK-QUOTED alias bound to `:Batch`. Before this fix the naming reader's
    # optional alias group could not match a backtick (so it read `{'Event'}` and passed) and the
    # write-alias reader found no `SET` head in `` `b`.embedding `` (so it asked about `e` and was
    # satisfied) — measured ACCEPTED at `9d389cc`, vector on `:Batch`.
    ("MATCH (e:Event {eventId:'seed'}), (`b`:Batch {id:'b1'}) "
     "SET e += $props, `b`.embedding = $embedding", {"Batch", "Event"},
     "outside the allowed set", "door stores on"),
    # A quoted LABEL spelling: `` :`Batch` `` is the same label to the engine.
    ("MATCH (`b`:`Batch` {id:$eid}) SET `b` += $props", {"Batch"},
     "outside the allowed set", "door stores on"),
    # ⛔ The sharpest row. The NAMED bound is fully satisfied here — one served label, no
    # interpolation — so ONLY the write-target half can refuse it: `` `p` `` is bound by
    # `CREATE (`p`)` with NO label, and the vector lands on a node no `entity_type` reaches.
    ("MATCH (`e`:Event {eventId:$eid}) CREATE (`p`) SET `p` += $props, "
     + set_embedding_clause("`p`"), {"Event"}, "NO LABEL", "WRITE TARGET"),
    # A label mutation spelled with quotes — the mutation reader had no head to read either.
    ("MATCH (`e`:Event {eventId:$eid}) REMOVE `e`:Event SET `e`:Foo, "
     + set_embedding_clause("`e`"), {"Event"}, "MUTATES", "WRITE TARGET"),
])
def test_the_door_refuses_a_BACKTICK_QUOTED_write_target(
        statement: str, named: set[str], problem_fragment: str, door_fragment: str) -> None:
    """A backtick-quoted alias is an IDENTIFIER to the engine, so the readers must see it.

    FalkorDB's lexer returns ``Token::IdentifierOrKeyword`` for a backticked span
    (`graph/src/parser/lexer.rs` @ `0f45a86`) — it does NOT read it as a string — so a reader that
    accepted only the bare spelling was blind to a spelling the engine accepts, which is the
    fail-OPEN direction for a bound on where a vector lands.

    ⛔ NON-VACUITY, asserted first: `statement_node_labels(statement) == named` (with the quoted
    label now READ, not skipped) and `statement_interpolates_label(...) is False`, so the shape is
    visible to the other two guards and it is the write-target half that refuses it.
    """
    assert statement_node_labels(statement) == named, statement_node_labels(statement)
    assert statement_interpolates_label(statement) is False, statement
    problem = statement_write_target_problem(statement, {"Event"})
    assert problem is not None, f"the reader did NOT see this store: {statement}"
    assert problem_fragment in problem, problem
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "_event_raw_write_door" in str(refused.value), (
        "the refusal must name the writer a reader can go look at"
    )
    assert door_fragment in str(refused.value), str(refused.value)


@pytest.mark.parametrize("statement", [
    # The product's own MERGE shape with EVERY identifier quoted, built with the real helpers — so
    # a fix that refused backticks wholesale (the cheap way to pass the refuse rows above) reds.
    "MERGE (`e`:Event {eventId:$eid}) " + clear_then_set_prefix("`e`")
    + "SET `e` += $props, " + set_embedding_clause("`e`"),
    "MATCH (`e`:Event {eventId:$eid}) SET `e` += $props",
])
def test_a_BACKTICK_QUOTED_real_shape_is_still_inside_the_bound(statement: str) -> None:
    """The other direction: the engine's other identifier spelling must not be refused.

    This row is load-bearing against the wrong fix. A guard that simply refused every backtick
    would pass all four refuse rows while breaking a spelling FalkorDB accepts and a caller may
    legitimately use — so the quoted product shape must reach the journal exactly as the bare one
    does, and `` REMOVE `e`.embedding `` must not be read as a label mutation.
    """
    assert statement_node_labels(statement) == {"Event"}, statement
    assert statement_pattern_labels(statement) == {"e": frozenset({"Event"})}, (
        statement_pattern_labels(statement))
    assert statement_write_aliases(statement) == {"e"}, statement_write_aliases(statement)
    assert statement_label_mutations(statement) == frozenset(), (
        statement_label_mutations(statement))
    assert statement_unattributed_set_items(statement) == (), statement
    assert statement_write_target_problem(statement, {"Event"}) is None, (
        statement_write_target_problem(statement, {"Event"}))
    with pytest.raises(AssertionError, match="journal was reached"):
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")


@pytest.mark.parametrize("statement", [
    # A BLOCK comment carrying an unbalanced `)` — the shape that used to delete every following
    # clause from the depth scan, leaving the reader satisfied by the one target before it.
    ("MATCH (e:Event {eventId:$eid}) SET e += $props /* ) */ CREATE (p) "
     "SET p.embedding = $embedding"),
    # The same in a LINE comment.
    ("MATCH (e:Event {eventId:$eid}) SET e += $props // )\nCREATE (p) "
     "SET p.embedding = $embedding"),
])
def test_a_CYPHER_COMMENT_cannot_hide_a_write_target(statement: str) -> None:
    """A comment is not syntax — reading one as syntax is a fail-open, and it was live.

    `_depths` decrements on every `)` outside a quote, so a `)` inside `/* … */` drove the depth
    negative and every following character read as "quoted": the `CREATE (p)` + `SET p.embedding`
    after the comment became invisible, the reader's only target was `e`, and the door ACCEPTED while
    the vector landed on the label-less node `p` (measured at `9d389cc`). FalkorDB's lexer skips
    `// …` and `/* … */` between tokens, so blanking them is what the engine does too.

    ⛔ NON-VACUITY: `p` appearing in the write aliases is the proof the comment is gone — before the
    fix this set was `{'e'}`.
    """
    assert "p" in statement_write_aliases(statement), (
        f"the commented-out clause is still invisible: {sorted(statement_write_aliases(statement))}"
    )
    problem = statement_write_target_problem(statement, {"Event"})
    assert problem is not None and "NO LABEL" in problem, problem
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "WRITE TARGET" in str(refused.value), str(refused.value)


def test_a_set_item_with_NO_alias_head_is_refused() -> None:
    """A `SET` item the reader cannot attribute is refused, not skipped.

    Cypher permits a PropertyExpression whose atom is a parenthesised expression, so `` SET (CASE
    WHEN $c THEN p ELSE e END).embedding = $embedding `` names no alias. A reader that skipped such
    an item would prove a target from the OTHER items (here a labelled `CREATE (p:Event …)`) while
    the vector actually landed on `e` or wherever the expression pointed — the same false PASS as an
    invisible alias, one grammar level up. Fail-closed is the only safe reading.
    """
    statement = ("MATCH (e:Event {eventId:$eid}) CREATE (p:Event {eventId:$x}) "
                 "SET (CASE WHEN $c THEN p ELSE e END).embedding = $embedding")
    # The other two guards are satisfied — that is the point: only the attribution rule refuses.
    assert statement_node_labels(statement) == {"Event"}, statement_node_labels(statement)
    assert statement_interpolates_label(statement) is False, statement
    assert statement_unattributed_set_items(statement), statement
    problem = statement_write_target_problem(statement, {"Event"})
    assert problem is not None and "not a plain alias" in problem, problem
    with pytest.raises(ValueError) as refused:
        DOOR(_NoJournal(), None, statement, event_id="e1", props={},
             embedding=VEC, lane="test")
    assert "WRITE TARGET" in str(refused.value), str(refused.value)


def test_a_real_event_write_with_a_QUOTED_alias_lands_on_event(tmp_path) -> None:
    """The quoted spelling is real: the ENGINE accepts it, and the write stays reachable.

    The readers are text readers, so the one thing they cannot prove by themselves is that a
    backtick-quoted alias means what the lexer says it means. This writes through the door with every
    identifier quoted and reads the vector back out of `run_vector_query` — so a "fix" that only
    taught the readers a spelling the engine rejects would red here instead of shipping.
    """
    sdk = TortoiseSDK(str(tmp_path / f"{uuid4().hex}.db"))
    try:
        proj = sdk._get_proj()
        written = sdk._event_raw_write_door(
            proj,
            "MERGE (`e`:Event {eventId:$eid}) " + clear_then_set_prefix("`e`")
            + "SET `e` += $props, " + set_embedding_clause("`e`"),
            event_id="evt-11641-bt",
            props={"eventId": "evt-11641-bt", "eventKind": "DocumentCreated"},
            event_kind="DocumentCreated",
            embedding=VEC,
            lane="test-11641-bt",
        )
        assert written is True
        rows = proj.g.query(
            "MATCH (`e`:Event {eventId:'evt-11641-bt'}) RETURN typeof(`e`.embedding)"
        ).result_set
        assert rows and rows[0][0] == "Vectorf32", rows
        hits = run_vector_query(proj.g, VEC, limit=10, is_embedded=True,
                                entity_type="event", excluded_statuses=())
        assert "evt-11641-bt" in [hit[0] for hit in hits], (
            f"the quoted-alias write is not reachable through the query leg: {hits}"
        )
    finally:
        sdk.close()


# ── restore_graph: refused artifact, and the legitimate one it must still restore ─────────────

def _dump(nodes: list[dict]) -> dict:
    return {"format": DUMP_FORMAT, "nodes": nodes, "edges": []}


def _proj(tmp_path) -> FalkorProjection:
    return FalkorProjection(str(tmp_path / f"{uuid4().hex}.db"))


@pytest.mark.parametrize("labels", [
    ["Batch"],            # a label nothing queries
    ["Session"],          # the other unserved label the register knows about
    [],                   # unlabeled: reachable by no entity_type at all
])
def test_restore_graph_refuses_an_unserved_label(tmp_path, labels) -> None:
    """A dump carrying a vector on an unqueryable label is REFUSED, before any mutation.

    Before this bound the restore wrote it silently and returned node/edge counts: a vector paid
    for and unreadable, restored through the one operation that could have noticed it.
    """
    proj = _proj(tmp_path)
    artifact = _dump([{"dump_id": 1, "labels": labels,
                       "props": {"id": "x", "embedding": VEC}}])
    with pytest.raises(ValueError) as refused:
        restore_graph(proj.g, artifact)
    assert "no read path" in str(refused.value) or "run_vector_query" in str(refused.value), (
        str(refused.value)
    )
    # PRE-MUTATION: the refusal must leave the target holding nothing but its own export-skip
    # bookkeeping (`FalkorProjection` creates a `:Meta{key:'point_fts_v2'}` marker at construction,
    # which `restore_graph`'s own emptiness check tolerates) — the #3895 discipline, extended to the
    # vector half.
    from tortoise.hosted_api import _is_export_skip_node
    left = [
        list(row[0] or []) for row in proj.g.query(
            "MATCH (n) RETURN labels(n), properties(n)").result_set
        if not _is_export_skip_node(list(row[0] or []), dict(row[1] or {}))
    ]
    assert left == [], (
        f"the refusal left {left} behind — the vector check must run before any node is created, "
        "or a rejected artifact leaves a partially restored graph"
    )


def test_a_pre_d10_document_dump_still_restores_under_the_repair_path(tmp_path) -> None:
    """``#11641`` review finding: the refusal must not make a REAL artifact unrestorable.

    The refusal above is correct for a dump that should never have been written — but a dump written
    BEFORE the D10 relabel still holds ``(:Document {id, ...})`` with an ``embedding`` (``:Document``
    was a vector seam before ``#4524``), and the documented repair is
    ``migrate_kinds.relabel_legacy_documents(sdk)`` run ON THE RESTORED GRAPH. A refusal with no way
    past it therefore does not protect the vector — it makes every OTHER node and edge in that
    backup unrecoverable and blocks the migration that would have fixed it. `allow_dangling_edges`
    is the precedent (`#3895`): the same class of artifact, the same explicit, non-silent repair.
    """
    proj = _proj(tmp_path)
    artifact = _dump([
        {"dump_id": 1, "labels": ["Document"],
         "props": {"id": "d1", "documentKind": "article", "embedding": VEC}},
        {"dump_id": 2, "labels": ["Point"],
         "props": {"id": "p1", "content": "a point", "pointKind": "claim",
                   "embedding": VEC}},
    ])
    counts = restore_graph(proj.g, artifact, allow_unserved_vectors=True)
    # The NODES are restored — that is the half the migration needs.
    assert counts["nodes"] == 2, counts
    # …the unserved VECTOR is dropped, and reported rather than silent.
    assert counts["dropped_vectors"] == 1, counts
    assert counts["dropped_vector_labels"] == ["Document"], counts
    # `__dump_id` is the restore's own bookkeeping and is swept at the end, so query the node by
    # the identity the dump carried.
    rows = proj.g.query(
        "MATCH (d:Document {id:'d1'}) RETURN typeof(d.embedding), d.id, d.documentKind"
    ).result_set
    assert rows and rows[0][0] == "Null", (
        "the unserved vector survived the repair path — it would land where no read path reaches"
    )
    assert (rows[0][1], rows[0][2]) == ("d1", "article"), rows
    # …and the served one still landed, so the flag is not a blanket disable.
    served = proj.g.query(
        "MATCH (p:Point {id:'p1'}) RETURN typeof(p.embedding)").result_set
    assert served and served[0][0] == "Vectorf32", served


def test_restore_graph_still_restores_a_served_label(tmp_path) -> None:
    """The legitimate direction, for EVERY served label AND a multi-label node.

    "Every" is not rhetorical: the node set is built from `_VECTOR_RESTORE_LABELS`, so adding a
    served label widens this test instead of leaving a served label that no dump ever exercises
    (the first draft here named only `Point` and `Event` while its docstring claimed the whole set).

    The multi-label case is not decoration: `MATCH (n:Point)` matches a node that also carries
    `:Foo`, so such a vector IS reachable and must restore — grouped under its SERVED label.
    """
    proj = _proj(tmp_path)
    nodes: list[dict] = []
    for i, (label, id_prop) in enumerate(sorted(_VECTOR_RESTORE_LABELS), start=1):
        nodes.append({"dump_id": i, "labels": [label],
                      "props": {id_prop: f"v{i}", "embedding": VEC}})
    nodes += [
        {"dump_id": 90, "labels": ["Point", "TestMarker"],
         "props": {"id": "p3", "content": "multi-label", "pointKind": "claim",
                   "embedding": VEC}},
        {"dump_id": 91, "labels": ["Subject"], "props": {"id": "s1", "name": "no vector"}},
    ]
    counts = restore_graph(proj.g, _dump(nodes))
    assert counts["nodes"] == len(nodes), counts

    for i, (label, id_prop) in enumerate(sorted(_VECTOR_RESTORE_LABELS), start=1):
        rows = proj.g.query(
            f"MATCH (n:{label} {{{id_prop}:$v}}) RETURN typeof(n.embedding)",
            params={"v": f"v{i}"},
        ).result_set
        assert rows and rows[0][0] == "Vectorf32", (label, id_prop, rows)
    # …the multi-label node too, under its served label.
    rows = proj.g.query(
        "MATCH (n:Point {id:'p3'}) RETURN typeof(n.embedding)").result_set
    assert rows and rows[0][0] == "Vectorf32", rows
    # …and the label-scoped write did not smear: the node that carried NO vector still carries none.
    assert proj.g.query(
        "MATCH (n:Subject {id:'s1'}) RETURN n.embedding"
    ).result_set[0][0] is None


def test_an_unserved_vector_restores_when_it_carries_no_embedding(tmp_path) -> None:
    """The bound is on the VECTOR, not on the label: an unserved label with no vector is fine.

    Without this the guard would be a new restriction on legitimate dumps — `:Batch` markers,
    `:Session` nodes and every bookkeeping label restores exactly as before.
    """
    proj = _proj(tmp_path)
    counts = restore_graph(proj.g, _dump([
        {"dump_id": 1, "labels": ["Batch"], "props": {"id": "b1", "createdAt": "2026-01-01"}},
    ]))
    assert counts["nodes"] == 1, counts
    assert proj.g.query(
        "MATCH (b:Batch {id:'b1'}) RETURN b.embedding"
    ).result_set[0][0] is None
