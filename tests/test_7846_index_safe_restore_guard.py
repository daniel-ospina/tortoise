"""#7846 — the index-safe graph-key restore, hermetic (no server).

WHY THIS FILE EXISTS, AND WHAT THE LIVE FILE DOES NOT PIN. ``tests/test_indexed_graph_key_restore_7846.py``
proves that a restore of an index-bearing graph RETURNS, keeps the instance answering, and
leaves the index queryable. What it cannot cheaply prove is the thing that makes the restore
*not blocked at all*: that the index was removed **before** the ``DUMP``, so the payload
carried none and the engine had nothing to rebuild on its serving thread.

A duration assertion cannot prove that either, at any size CI will run: measured on the
issue's engine (``falkordb/falkordb:latest``, graph module 42201, 2026-10-17, see
``docs/evidence/7846-indexed-graph-restore/drop-first-42201-5000.log``), the indexed RESTORE
is 0.599 s at 5 000 nodes and the index-free one 0.036 s — 16.6x, but both far inside any
bound generous enough not to false-red a slow runner. So the ORDER is pinned here, as
behaviour, on a fake engine that records every call.

WHAT THESE TESTS PIN, and the mutation each one reds:

* the raw driver REFUSES an indexed graph, before ``dump()``/``delete()`` — mutate the guard
  to consult the catalogue after ``dump()`` or to ignore it, and tests 1, 2 and 4 red;
* the guard does NOT fire on an unindexed graph — mutate it to always refuse and test 3 reds
  (that is the "refuse everything, look safe" degenerate fix);
* the safe path DROPS before the ``DUMP`` — skip the drop and test 5 reds on the catalogue
  state sampled at ``dump()`` time. That is the mutation the issue names;
* the re-created index is the SAME definition that was read (``dimension``,
  ``similarityFunction``, ``M``, ``efConstruction``, ``efRuntime``) — hardcode
  ``{dimension, similarityFunction}`` and tests 6 and 9 red;
* an unreadable definition refuses BEFORE anything is dropped, and a drop that does not
  take refuses BEFORE the ``DUMP`` — fail-open either one and tests 7 and 8 red;
* a restore that RAISES does not re-create the index over a deleted graph key, and hands the
  caller the ``CREATE`` statement — test 10 reds if recreation is attempted there.
"""

from __future__ import annotations

import pytest

from tests import test_indexed_graph_key_restore_7846 as live
from tortoise.graph_key_restore import (
    DEFAULT_RESTORE_BOUND_S,
    SERVED,
    UNMEASURED,
    IndexDefinitionUnreadable,
    IndexDropIncompleteError,
    IndexDropUnverifiedError,
    IndexedGraphRestoreRefused,
    IndexRestoreMismatchError,
    IndexRestoreUnverifiedError,
    _append_state_to_message,
    restore_graph_key,
    restore_graph_key_safe,
    same_index_definitions,
    vector_index_definitions,
)

#: A catalogue row for a NODE vector index, in the shape the engine returns it: everything
#: after the label is STRINGIFIED (`[embedding]`, `{embedding: [VECTOR]}`). The non-default
#: HNSW knobs are the point — `M: 8, efConstruction: 64, efRuntime: 4` measured off graph
#: module 42201 for an index created with those OPTIONS, so a definition that "re-creates"
#: with the engine's defaults (16/200/10) is a DIFFERENT index.
_INDEXED_ROWS = [
    [
        "Point",
        "[embedding]",
        "{embedding: [VECTOR]}",
        "{embedding: {dimension: 384, similarityFunction: euclidean, "
        "M: 8, efConstruction: 64, efRuntime: 4}}",
        "english",
        "[]",
        "NODE",
        "OPERATIONAL",
        "{}",
    ]
]

#: A graph with no index at all — the issue's own control case.
_UNINDEXED_ROWS: list = []

#: A SECOND vector index on the same graph, on another label — the multi-index case the
#: first review round found the fake engine could not express.
_SECOND_INDEX_ROW = [
    "Entity",
    "[vec]",
    "{vec: [VECTOR]}",
    "{vec: {dimension: 384, similarityFunction: cosine, M: 16, efConstruction: 200, "
    "efRuntime: 10}}",
    "english",
    "[]",
    "NODE",
    "OPERATIONAL",
    "{}",
]


def _row_key(row) -> str:
    """A stable identity for a raw catalogue row (its properties cell)."""
    try:
        return str(row[1])
    except (TypeError, IndexError):
        return str(row)


def _catalogue_row(definition, *, options=None) -> list:
    """The row an engine returns after ``CREATE VECTOR INDEX ... OPTIONS {...}``.

    ``options`` defaults to the definition's own map, i.e. "the engine applied exactly what
    it was told" — which is what makes the read-back verification a real check rather than a
    tautology. Passing ``options=`` models an emitter that silently changed them.
    """
    pairs = options if options is not None else definition.options
    inner = ", ".join(f"{key}: {value}" for key, value in pairs)
    return [
        definition.label,
        f"[{definition.property}]",
        "{" + f"{definition.property}: [VECTOR]" + "}",
        "{" + f"{definition.property}: {{{inner}}}" + "}",
        "english",
        "[]",
        definition.entity_type,
        "OPERATIONAL",
        "{}",
    ]


def _parsed_row(row) -> list:
    """A STRINGIFIED catalogue row as a PARSED client hands it over.

    ``falkordb``'s ``result_set`` is NATIVE: ``row[1]`` a ``list[str]``, ``row[2]`` a
    ``{field: [types]}`` dict and ``row[3]`` a ``{field: {option: value}}`` dict, with the
    option integers as ``int`` — the shape ``tortoise/index_catalogue.py`` records as
    MEASURED (``OrderedDict({'content': ['FULLTEXT'], ...})``). This helper lets a test hand
    the SAME catalogue over in both shapes; the pre-fix parser handled only the string one
    and refused every graph through the parsed seam (#8965 review).
    """
    field = str(row[1]).strip().strip("[]").strip()
    tokens = [
        token.strip()
        for token in str(row[2]).strip().split("[", 1)[1].rstrip("]}").split(",")
        if token.strip()
    ]
    options: dict[str, int | str] = {}
    options_cell = str(row[3]).strip()
    if options_cell not in ("", "{}"):
        inner = options_cell.split("{", 2)[2].rstrip("}").strip()
        for piece in inner.split(","):
            key, _, value = piece.partition(":")
            value = value.strip()
            options[key.strip()] = (
                int(value) if value.lstrip("-").isdigit() else value.strip("'\"")
            )
    return [
        row[0], [field], {field: tokens}, {field: options},
        str(row[4]), [], row[6], row[7], {},
    ]


class _FakeGraph:
    """A graph whose only real property is the ORDER and PRESENCE of the calls made.

    Holds a CATALOGUE OF ROWS rather than a bool, so it can model the cases the first
    review round found unrepresentable: two vector indexes, a drop that raises on the
    second, a create that raises on the second, a drop that answers OK and does nothing.
    """

    def __init__(self, rows=None, *, drop_works=True, echo=None, restore_error=None,
                 dump_error=None, drop_fails_on=(), create_fails_on=(),
                 create_applies_then_raises_on=(), fail_list_on=()):
        #: keyed by the properties cell so insertion order survives and a per-index drop
        #: can remove exactly one row
        self.rows: dict[str, list] = {
            _row_key(row): list(row) for row in (rows if rows is not None else [])
        }
        self.drop_works = drop_works
        self.echo = echo
        self.restore_error = restore_error
        self.dump_error = dump_error
        self.drop_fails_on = set(drop_fails_on)
        self.create_fails_on = set(create_fails_on)
        #: Properties whose CREATE TAKES EFFECT engine-side and STILL raises (a connection
        #: reset after the statement was accepted). Models `gone == []`: the catalogue
        #: proves every definition present although the call reported a failure.
        self.create_applies_then_raises_on = set(create_applies_then_raises_on)
        #: 1-based `list_indexes()` call numbers that RAISE — models a catalogue read that
        #: fails at a specific point in the sequence (a transient outage, the case where a
        #: failure must not be reported as a measured fact).
        self.fail_list_on = set(fail_list_on)
        self.list_calls = 0
        self.calls: list = []
        #: The catalogue AS IT WAS when `dump()` ran — the mutation detector for "skip the
        #: drop before the DUMP".
        self.catalogue_at_dump: list | None = None

    @property
    def catalogue(self) -> list:
        return list(self.rows.values())

    # --- the engine seams -------------------------------------------------------
    def list_indexes(self):
        self.calls.append("list_indexes")
        self.list_calls += 1
        if self.list_calls in self.fail_list_on:
            raise ConnectionError(f"catalogue read #{self.list_calls} failed")
        return [list(row) for row in self.rows.values()]

    def drop_index(self, definition):
        self.calls.append(f"drop:{definition.property}")
        if definition.property in self.drop_fails_on:
            raise RuntimeError(f"engine refused to drop {definition.property}")
        if not self.drop_works:
            return  # answers OK and does nothing — the silent no-op
        for key, row in list(self.rows.items()):
            parsed = vector_index_definitions([row], graph="g7846")
            if parsed and parsed[0].signature() == definition.signature():
                del self.rows[key]

    def create_index(self, definition):
        self.calls.append(f"create:{definition.property}")
        if definition.property in self.create_fails_on:
            raise RuntimeError(f"engine refused to create {definition.property}")
        options = self.echo(definition) if self.echo is not None else None
        row = _catalogue_row(definition, options=options)
        self.rows[_row_key(row)] = row
        if definition.property in self.create_applies_then_raises_on:
            raise RuntimeError(
                f"engine: connection reset after CREATE {definition.property}"
            )

    def dump(self):
        self.calls.append("dump")
        self.catalogue_at_dump = [list(row) for row in self.rows.values()]
        if self.dump_error is not None:
            raise self.dump_error
        return b"x" * 64

    def delete(self):
        self.calls.append("delete")

    def restore(self, _payload):
        self.calls.append("restore")
        if self.restore_error is not None:
            raise self.restore_error
        return "OK"

    def ping(self):
        self.calls.append("ping")

    def listening(self):
        return True

    # --- conveniences -----------------------------------------------------------
    def destructive_calls(self) -> list:
        return [call for call in self.calls if call in ("dump", "delete", "restore")]

    def safe(self, **overrides):
        kwargs = dict(
            graph="g7846",
            dump=self.dump,
            delete=self.delete,
            restore=self.restore,
            ping=self.ping,
            list_indexes=self.list_indexes,
            drop_index=self.drop_index,
            create_index=self.create_index,
            bound_s=5.0,
            poll_interval_s=0.01,
            witness_join_s=0.2,
            recovery_probe_s=0.05,
        )
        kwargs.update(overrides)
        return restore_graph_key_safe(**kwargs)

    def raw(self, **overrides):
        kwargs = dict(
            graph="g7846",
            dump=self.dump,
            delete=self.delete,
            restore=self.restore,
            ping=self.ping,
            index_guard=self.list_indexes,
            bound_s=5.0,
            poll_interval_s=0.01,
            witness_join_s=0.2,
            recovery_probe_s=0.05,
        )
        kwargs.update(overrides)
        return restore_graph_key(**kwargs)


# ---------------------------------------------------------------------------------
# 1-4: the REFUSAL GUARD on the raw path
# ---------------------------------------------------------------------------------


def test_the_guard_refuses_an_indexed_graph_before_anything_is_deleted() -> None:
    """The guard is only a guard if it runs FIRST — the sequence deletes on step two.

    A check that fired after ``dump()`` would already have the payload in hand and a check
    after ``delete()`` would already have destroyed the graph key, so "refused" would then
    mean "refused after losing the graph". Pre-fix (no guard at all) this test's
    ``pytest.raises`` block fails: the restore proceeds and the graph is restored over its
    own index.
    """
    engine = _FakeGraph(_INDEXED_ROWS)

    with pytest.raises(IndexedGraphRestoreRefused) as raised:
        engine.raw()

    assert engine.destructive_calls() == [], (
        f"the refusal ran AFTER a destructive step: {engine.calls}"
    )
    message = str(raised.value)
    assert "g7846" in message, "the refusal does not name the graph"
    assert "Point" in message and "embedding" in message, (
        f"the refusal does not name the index: {message}"
    )
    assert "restore_graph_key_safe" in message, (
        f"the refusal does not name the path that can do it safely: {message}"
    )
    assert "NOTHING HAS BEEN DELETED" in message, (
        f"the refusal does not say the graph is intact: {message}"
    )
    assert len(raised.value.definitions) == 1
    assert raised.value.definitions[0].dimension == 384


def test_the_guard_does_not_fire_on_an_unindexed_graph() -> None:
    """THE DEGENERATE FIX IS "always refuse", and that is not a fix.

    An index-free graph is the case the issue measured as safe (0.17 s restore, no block),
    so refusing it would take the safe path away from the graphs that do not need it. Mutate
    the guard to refuse unconditionally and this test reds — the restore never runs.
    """
    engine = _FakeGraph(_UNINDEXED_ROWS)

    observation = engine.raw()

    assert engine.destructive_calls() == ["dump", "delete", "restore"], (
        f"the unindexed restore did not run: {engine.calls}"
    )
    assert observation.status in (SERVED, UNMEASURED)


def test_the_guard_reads_whatever_catalogue_shape_the_caller_has_to_hand() -> None:
    """Three shapes reach this seam, and rejecting one is how a caller drops the guard.

    * the ROWS of a raw ``GRAPH.QUERY`` reply — the live file's shape;
    * the FULL reply (``[header, rows, stats]``) — the most obvious thing to pass;
    * a ``falkordb`` client's ``QueryResult`` (``.result_set``).

    Pre-fix the middle one raised ``IndexDefinitionUnreadable`` on the HEADER row, which
    reads as a catalogue defect and invites "fixing" it by deleting the guard.
    """
    rows = [list(row) for row in _INDEXED_ROWS]
    full_reply = [["label", "properties", "types", "options"], rows, ["stats"]]
    # A PARSED client's `result_set` is NOT the string rows with an attribute on top: its
    # cells are native (dict/list), which is what the pre-fix parser could not read. Building
    # this case out of the string rows (the pre-fix form of this test) exercised only the
    # attribute unwrap and passed on the broken parser (#8965 review).
    parsed = [_parsed_row(row) for row in _INDEXED_ROWS]

    class _QueryResult:
        result_set = parsed

    for seam in (rows, full_reply, _QueryResult()):
        engine = _FakeGraph(_UNINDEXED_ROWS)
        with pytest.raises(IndexedGraphRestoreRefused):
            engine.raw(index_guard=lambda seam=seam: seam)
        assert engine.destructive_calls() == [], (
            f"a {type(seam).__name__} catalogue did not stop the restore: {engine.calls}"
        )


def test_the_PARSED_client_shape_parses_to_the_SAME_definition() -> None:
    """The documented ``result_set`` seam must read, not refuse (#8965 review P2).

    The parsed cells reach this module through the client a caller actually holds, and a
    string-only parser refused EVERY graph through it — including the RANGE-only graphs the
    non-vector skip exists to protect — with "carries an index type this module does not
    recognise ('VECTOR')". Pre-fix this test raises ``IndexDefinitionUnreadable``.
    """
    string_definition = vector_index_definitions(_INDEXED_ROWS, graph="g7846")[0]
    parsed_definition = vector_index_definitions(
        [_parsed_row(_INDEXED_ROWS[0])], graph="g7846"
    )[0]

    assert parsed_definition.signature() == string_definition.signature()
    assert parsed_definition.create_cypher() == string_definition.create_cypher(), (
        "the parsed shape re-creates a different statement"
    )
    assert parsed_definition.option("M") == 8
    assert isinstance(parsed_definition.option("M"), int), (
        "the option lost its integer type through the parsed seam"
    )

    # ... and the SAFE path runs end-to-end through it, not merely the parser.
    engine = _FakeGraph(_INDEXED_ROWS)
    result = engine.safe(list_indexes=lambda: [_parsed_row(r) for r in engine.catalogue])

    assert engine.catalogue_at_dump == []
    assert result.indexes.recreated is True and result.indexes.verified is True


def test_a_range_index_in_the_PARSED_shape_is_not_a_reason_to_refuse() -> None:
    """Every production graph carries a RANGE index, so this arm decides adoptability.

    ``test_a_range_or_fulltext_index_is_not_a_reason_to_refuse`` pins the rule for the
    string shape; the parsed shape is what a caller's own client gives it, and the pre-fix
    parser refused it — i.e. the safe path was unusable on every production graph through
    the seam the module documents.
    """
    rangeful = [
        "Point", ["is_operator"], {"is_operator": ["RANGE"]}, {},
        "english", [], "NODE", "OPERATIONAL", {},
    ]
    engine = _FakeGraph(_UNINDEXED_ROWS)

    observation = engine.raw(index_guard=lambda: [rangeful])

    assert engine.destructive_calls() == ["dump", "delete", "restore"], (
        f"a RANGE-only graph was refused through the parsed seam: {engine.calls}"
    )
    assert observation.status in (SERVED, UNMEASURED)


def test_a_guard_that_cannot_read_the_catalogue_refuses_rather_than_passing() -> None:
    """FAIL CLOSED. "I could not read the catalogue" must never become "no index".

    The driver deliberately does not catch a raising guard: swallowing it would turn the
    one fail-closed check in this path into a fail-open one, and the caller would get an
    index-bearing restore believing it had been checked. Nothing destructive may run.
    """
    engine = _FakeGraph(_INDEXED_ROWS)

    def cannot_read():
        raise ConnectionError("catalogue unreachable")

    with pytest.raises(ConnectionError):
        engine.raw(index_guard=cannot_read)

    assert engine.destructive_calls() == [], (
        f"an unreadable catalogue did not stop the restore: {engine.calls}"
    )


def test_a_range_or_fulltext_index_is_not_a_reason_to_refuse() -> None:
    """Only VECTOR indexes rebuild on the serving thread, so only they are the hazard.

    Refusing on any index at all would take the safe path away from every production graph
    (they all carry the ``is_operator`` boolean index), which is how a safety check gets
    disabled in practice.
    """
    rangeful = [
        ["Point", "[is_operator]", "{is_operator: [RANGE]}", "{}", "english", "[]",
         "NODE", "OPERATIONAL", "{}"],
    ]
    engine = _FakeGraph(rangeful)

    observation = engine.raw()

    assert engine.destructive_calls() == ["dump", "delete", "restore"]
    assert observation.status in (SERVED, UNMEASURED)


# ---------------------------------------------------------------------------------
# 5-6: the SAFE PATH's order and its definition fidelity
# ---------------------------------------------------------------------------------


def test_the_safe_path_drops_the_index_BEFORE_the_dump() -> None:
    """THE LOAD-BEARING MUTATION: skip the drop and the payload carries the index.

    The index travels INSIDE the graph key's serialized payload, so it has to be off the
    graph before the ``DUMP`` (an after-the-fact drop would restore the index and rebuild it
    on the serving thread — the block this path exists to avoid). Sampled at ``dump()`` time
    rather than inferred from call order, because the harmful state is "the catalogue is
    non-empty when the payload is taken", not the order of two log lines.
    """
    engine = _FakeGraph(_INDEXED_ROWS)

    result = engine.safe()

    assert engine.catalogue_at_dump == [], (
        f"the DUMP ran with an index still on the graph ({engine.catalogue_at_dump}) — the "
        f"payload carried the index, so the RESTORE rebuilds it on the serving thread"
    )
    assert engine.calls.index("drop:embedding") < engine.calls.index("dump")
    assert result.indexes.dropped is True
    assert result.indexes.recreated is True
    assert result.indexes.verified is True


def test_the_safe_path_re_creates_the_SAME_definition_it_read() -> None:
    """Read, never hardcode: the definition is the catalogue's, HNSW knobs included.

    ``{dimension: 384, similarityFunction: 'euclidean'}`` is what a hand-written
    ``CREATE`` looks like — and it silently re-creates the index with ``M: 16,
    efConstruction: 200, efRuntime: 10`` instead of the measured ``8/64/4``. The index then
    reports ``OPERATIONAL`` and answers with different recall. Pre-fix (hardcoded OPTIONS)
    every assertion on ``M``/``efConstruction``/``efRuntime`` here fails.
    """
    engine = _FakeGraph(_INDEXED_ROWS)

    result = engine.safe()

    definition = result.indexes.definitions[0]
    assert definition.dimension == 384
    assert definition.similarity_function == "euclidean"
    assert definition.option("M") == 8
    assert definition.option("efConstruction") == 64
    assert definition.option("efRuntime") == 4

    create = definition.create_cypher()
    assert "OPTIONS {dimension: 384" in create
    assert "similarityFunction: 'euclidean'" in create, (
        f"the metric is not quoted, so the CREATE is not valid Cypher: {create}"
    )
    assert "M: 8" in create and "efConstruction: 64" in create and "efRuntime: 4" in create, (
        f"the HNSW parameters were not carried into the re-create: {create}"
    )
    assert definition.drop_cypher() == "DROP VECTOR INDEX FOR (p:Point) ON (p.embedding)", (
        "the drop is not the VECTOR form — the plain `DROP INDEX ON :Label(prop)` form "
        "answers 'no such index' for a vector index on graph module 42201, which would "
        "leave the index in place and the restore index-bearing"
    )
    assert result.indexes.as_dict()["definitions"][0]["options"]["M"] == 8


def test_an_unindexed_graph_is_restored_without_any_index_work() -> None:
    """The safe path must be usable on every graph, not only on indexed ones."""
    engine = _FakeGraph(_UNINDEXED_ROWS)

    result = engine.safe()

    assert engine.destructive_calls() == ["dump", "delete", "restore"]
    assert result.indexes.definitions == ()
    assert result.indexes.dropped is False
    assert result.indexes.recreated is False
    assert result.indexes.verified is None
    assert "no VECTOR index" in result.indexes.sentence()


# ---------------------------------------------------------------------------------
# 7-10: the ways the safe path must FAIL instead of proceeding
# ---------------------------------------------------------------------------------


def test_an_unreadable_vector_definition_refuses_before_ANY_step() -> None:
    """A definition that cannot be re-created must not be dropped.

    The catalogue's OPTIONS cell is a stringified map, and this module parses it. If a future
    engine changes that shape, the failure has to be a REFUSAL: dropping the index and then
    failing to re-create it would leave the graph permanently degraded — worse than the block
    the path avoids. Pre-fix (no parse, or a permissive one) the index is dropped and the
    exception fires with the graph already index-less.
    """
    engine = _FakeGraph(
        [
            ["Point", "[embedding]", "{embedding: [VECTOR]}", "vector-ish", "english",
             "[]", "NODE", "OPERATIONAL", "{}"],
        ]
    )

    with pytest.raises(IndexDefinitionUnreadable) as raised:
        engine.safe()

    assert engine.calls == ["list_indexes"], (
        f"the safe path touched the graph with an unreadable definition: {engine.calls}"
    )
    assert "NOTHING HAS BEEN TOUCHED" in str(raised.value)


def test_a_drop_that_does_not_take_refuses_before_the_dump() -> None:
    """Re-read after the drop, and refuse — the re-read is what makes the drop load-bearing.

    A ``DROP`` that answers ``OK`` and leaves the index (a build still ``UNDER
    CONSTRUCTION``, a registry that disagrees with the catalogue) is exactly the silent
    no-op that would make this path look fixed while restoring an index-bearing payload.
    """
    engine = _FakeGraph(_INDEXED_ROWS, drop_works=False)

    with pytest.raises(IndexDropIncompleteError) as raised:
        engine.safe()

    assert "dump" not in engine.calls, (
        f"the DUMP ran although the index survived the drop: {engine.calls}"
    )
    assert "delete" not in engine.calls
    assert "embedding" in str(raised.value)


def test_a_re_created_index_that_lost_its_HNSW_parameters_is_an_error() -> None:
    """``verified`` compares the FULL options map, not "an index exists".

    Models an emitter (or an engine) that accepts the ``CREATE`` and applies its own
    defaults: the index comes back ``OPERATIONAL``, so a presence check would green it while
    every query on the restored graph now has different recall.
    """
    engine = _FakeGraph(
        _INDEXED_ROWS,
        # the engine applies its defaults instead of the options it was given
        echo=lambda _definition: (
            ("dimension", 384),
            ("similarityFunction", "euclidean"),
            ("M", 16),
            ("efConstruction", 200),
            ("efRuntime", 10),
        ),
    )

    with pytest.raises(IndexRestoreMismatchError) as raised:
        engine.safe()

    message = str(raised.value)
    assert "M: 8" in message or "efConstruction" in message, (
        f"the mismatch does not name what changed: {message}"
    )
    # The graph itself was restored and must not be blamed for the index defect.
    assert "do NOT re-issue the restore" in message


def test_a_failed_restore_does_not_re_create_the_index_over_a_deleted_graph() -> None:
    """``FAILED`` means the graph key is ALREADY DELETED — the index must not be re-created.

    The sequence is ``dump()`` → ``delete()`` → ``restore()``. When the restore raises, the
    key is gone and the index definition is the only thing left to hand the caller;
    re-creating the index then would build it over an empty graph shell (or race the restore
    the driver abandoned). Pre-fix (re-create unconditionally after the try) this test's
    ``create`` assertion fails.
    """
    engine = _FakeGraph(
        _INDEXED_ROWS, restore_error=RuntimeError("BUSYKEY destination key already exists")
    )

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    assert "BUSYKEY" in str(raised.value), "the engine's own error was replaced"
    creates = [call for call in engine.calls if call.startswith("create:")]
    assert creates == [], (
        f"the index was re-created after a FAILED restore, over a deleted graph key: {engine.calls}"
    )
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "CREATE VECTOR INDEX" in notes, (
        f"the dropped index definition was not handed to the caller: {notes!r}"
    )
    assert "NOT re-created" in notes
    assert "may ALREADY BE DELETED" in notes, (
        f"the note does not warn that the graph key may be gone — the caller would "
        f"re-create an index over a graph it assumes exists: {notes!r}"
    )


def test_a_dump_failure_re_creates_the_index_because_the_KEY_was_never_deleted() -> None:
    """#8965 review F5: the post-drop note described a state that was not the case.

    ``dump()`` runs BEFORE ``delete()``, so a ``dump()`` that raises leaves the graph key
    exactly where it was — the one post-drop state where putting the index back restores the
    status quo perfectly. Telling the caller "the restore did not return, re-create it once
    the graph is back" about a graph that was never touched invites it to abandon an index
    that is one ``CREATE`` away.
    """
    engine = _FakeGraph(_INDEXED_ROWS, dump_error=RuntimeError("DUMP failed"))

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    assert "DUMP failed" in str(raised.value)
    assert "delete" not in engine.calls, "the DEL ran after the DUMP failed"
    creates = [call for call in engine.calls if call.startswith("create:")]
    assert creates == ["create:embedding"], (
        f"the dropped index was not put back although the graph key was never deleted: "
        f"{engine.calls}"
    )
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "NEVER deleted" in notes, f"the note does not say the key survived: {notes!r}"
    assert "ALREADY BE DELETED" not in notes


def test_a_drop_that_RAISES_halfway_puts_back_what_it_dropped() -> None:
    """#8965 review F3: a raising drop loop used to lose index #1 with no trace.

    ``dropped`` was set only AFTER the loop completed, so a ``drop_index`` that raised on the
    second index propagated the engine's raw error with ``dropped=False``: the note was
    skipped and the first index — already dropped — was never mentioned or restored.
    """
    engine = _FakeGraph(
        [list(_INDEXED_ROWS[0]), list(_SECOND_INDEX_ROW)], drop_fails_on={"vec"}
    )

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    assert "refused to drop vec" in str(raised.value)
    assert "dump" not in engine.calls, "the DUMP ran although the drop loop raised"
    creates = [call for call in engine.calls if call.startswith("create:")]
    assert creates == ["create:embedding"], (
        f"the index that WAS dropped before the raise was not put back: {engine.calls}"
    )
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "re-created" in notes and "NEVER deleted" in notes, (
        f"the note does not report the index that was restored: {notes!r}"
    )


def test_two_indexes_where_one_drop_does_nothing_names_the_index_it_LOST() -> None:
    """#8965 review F2: the recovery re-created ``remaining``, i.e. the SURVIVOR.

    With two indexes and a drop that takes effect for one only, the old code re-created the
    one that had never been dropped (failing, and swallowed) and left the one that HAD been
    dropped gone — while the message claimed the graph was untouched.
    """
    engine = _FakeGraph([list(_INDEXED_ROWS[0]), list(_SECOND_INDEX_ROW)])

    # Drop the first for real, answer OK-and-do-nothing for the second.
    real_drop = engine.drop_index

    def _partially_broken(definition):
        if definition.property == "vec":
            engine.calls.append("drop:vec")
            return
        real_drop(definition)

    with pytest.raises(IndexDropIncompleteError) as raised:
        engine.safe(drop_index=_partially_broken)

    error = raised.value
    assert [str(definition.property) for definition in error.remaining] == ["vec"]
    # The DROPPED-and-gone index is the one that must be put back, and the SURVIVOR must not
    # be re-created (pre-fix it was: `create:vec` appears and this fails).
    assert "create:embedding" in engine.calls, (
        f"the index that WAS dropped was not put back: {engine.calls}"
    )
    assert "create:vec" not in engine.calls, (
        f"the recovery re-created the index that had never been dropped, leaving the lost "
        f"one gone: {engine.calls}"
    )
    assert [str(definition.property) for definition in error.restored] == ["embedding"]
    assert error.failed == (), (
        "the re-create succeeded, so nothing should be reported as lost"
    )
    assert "dump" not in engine.calls


def test_a_re_create_that_RAISES_halfway_names_what_it_could_not_restore() -> None:
    """#8965 review F3 (re-create arm): a partial re-create must not be a bare engine error.

    The graph has already been restored at this point, so the caller must be able to tell
    "the restore failed" from "the restore worked and one index is missing" — and must get
    the statement needed to finish the job.
    """
    engine = _FakeGraph(
        [list(_INDEXED_ROWS[0]), list(_SECOND_INDEX_ROW)], create_fails_on={"vec"}
    )

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    assert "refused to create vec" in str(raised.value)
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "NOT re-created" in notes and "vec" in notes, (
        f"the note does not name the index that is missing: {notes!r}"
    )
    assert "CREATE VECTOR INDEX" in notes, (
        f"the note carries no statement to finish the re-create: {notes!r}"
    )
    assert "WAS restored" in notes and "do NOT re-issue" in notes, (
        f"the note does not stop the caller re-issuing the restore: {notes!r}"
    )


def test_the_missing_index_is_in_the_MESSAGE_not_only_in_the_note() -> None:
    """#8965 review: a note alone is INVISIBLE to the caller — ``str(exc)`` omits ``__notes__``.

    ``BaseException.add_note`` fills ``__notes__``, which ONLY a formatted traceback
    renders. So the ordinary caller patterns — ``logger.error("%s", exc)``, ``return
    {"error": str(exc)}`` from an API, ``print(f"ERROR: {exc}")`` — received the engine's
    raw error and nothing about a graph now serving WITHOUT its vector index, which is the
    silent recall loss this whole path exists to prevent. The pre-existing tests proved the
    NOTE existed; they could not see that no caller ever receives it.

    The exception TYPE is kept (callers distinguish "the restore did not return" from "an
    index is missing" by type), so this asserts on the message text alone.
    """
    engine = _FakeGraph(
        [list(_INDEXED_ROWS[0]), list(_SECOND_INDEX_ROW)], create_fails_on={"vec"}
    )

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    message = str(raised.value)
    assert "NOT re-created" in message, (
        f"the index loss is note-only, so `str(exc)` — what every ordinary caller logs or "
        f"returns — says nothing about a graph that is now serving unindexed: {message!r}"
    )
    assert "vec" in message, message
    assert "CREATE VECTOR INDEX" in message, (
        f"the message carries no statement to finish the re-create: {message!r}"
    )
    # ... and the note is still there for the traceback reader.
    assert "NOT re-created" in " ".join(getattr(raised.value, "__notes__", [])), (
        "the note stopped being attached; keep both, the message AND the note"
    )


def test_a_create_that_takes_effect_and_STILL_raises_does_not_say_fix_the_index() -> None:
    """#8965 review: ``gone == []`` is REACHABLE, and the instruction must match it.

    A ``CREATE`` can take effect engine-side and still raise (a connection reset after the
    statement was accepted). ``_missing_from_catalogue`` then proves every definition
    PRESENT, so NO index is lost — and neither the message nor the note may tell the caller
    to "fix the index", which contradicts the very sets printed in the same breath.

    This arm is the one the sibling message test cannot reach: ``create_fails_on`` raises
    BEFORE the catalogue row appears, so it always leaves a non-empty ``gone`` and would
    pass with the guard deleted. The companion rule is pinned by
    ``test_the_missing_index_is_in_the_MESSAGE_not_only_in_the_note``: when an index IS
    missing the state must reach ``str(exc)``.
    """
    engine = _FakeGraph(
        [list(_INDEXED_ROWS[0])], create_applies_then_raises_on={"embedding"}
    )

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    message = str(raised.value)
    assert "fix the index only" not in message, (
        f"the message tells the caller to fix an index the catalogue proves PRESENT — and "
        f"it contradicts the sets it prints itself: {message!r}"
    )
    assert "NOT re-created" not in message, message
    assert "CREATE VECTOR INDEX" not in message, (
        f"the message carries a CREATE statement for an index the catalogue proves "
        f"PRESENT: {message!r}"
    )
    # THE GUARD ITSELF: nothing was lost, so the caller's own error must not be buried under
    # an index report. This is what an unconditional append (the pre-fix shape) fails.
    assert "PRESENT in the catalogue" not in message, (
        f"the index state was appended to the message although the catalogue proves NOTHING "
        f"was lost, so the caller's own error is buried under an index report: {message!r}"
    )

    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "re-created: " in notes, (
        f"the note stopped reporting the measured state: {notes!r}"
    )
    assert "fix the index only" not in notes, (
        f"the NOTE — what a formatted traceback renders — still instructs the caller to "
        f"fix an index the catalogue proves PRESENT: {notes!r}"
    )
    assert "PRESENT in the catalogue" in notes, (
        f"the note does not say the index is present, so the reader is left with a failure "
        f"and no account of the index: {notes!r}"
    )


def test_the_state_reaches_str_for_an_OSError_too() -> None:
    """#8965 review: the OSError family was the note-only hole one level down.

    ``ConnectionRefusedError``/``socket.gaierror`` is exactly what a DEAD or unreachable
    engine raises — the failure this module calls "the process died" (#8270) — and
    ``OSError.__str__`` renders ``errno``/``strerror`` rather than ``args``, so a guard keyed
    on ``args`` alone silently did nothing for the failure mode that matters most.
    """
    for exc in (
        OSError(111, "Connection refused"),
        ConnectionRefusedError(111, "Connection refused"),
        ValueError("plain message"),
    ):
        _append_state_to_message(exc, "the index is MISSING")
        assert "the index is MISSING" in str(exc), (
            f"{type(exc).__name__} still carries the loss only in __notes__: {str(exc)!r}"
        )

    # A MESSAGE-ONLY OSError subclass (`strerror is None`, message in `args[0]`) must keep
    # its own text: replacing it destroys the cause a caller needs to tell "engine dead"
    # (#8270) from "index missing", which is worse than the note-only state this fixes.
    for exc in (OSError("engine died"), TimeoutError("timed out"),
                ConnectionResetError("reset by peer")):
        original = str(exc)
        _append_state_to_message(exc, "the index is MISSING")
        assert original in str(exc), (
            f"{type(exc).__name__} lost its own message, so the caller cannot tell what "
            f"actually failed: {original!r} -> {str(exc)!r}"
        )
        assert "the index is MISSING" in str(exc), (
            f"{type(exc).__name__} did not gain the index state: {str(exc)!r}"
        )

    errno_exc = OSError(111, "Connection refused")
    _append_state_to_message(errno_exc, "state")
    assert errno_exc.errno == 111, (
        "errno must survive, or the caller loses the `[Errno 111]` the state was appended to"
    )


def test_a_blank_properties_cell_names_NO_property_and_a_repeat_names_ONE() -> None:
    """Two edge cases in the both-columns union (#8965 review).

    A BLANK ``properties`` cell must contribute NO name: returning a phantom ``('')`` made a
    RANGE-only graph REFUSE (the phantom is absent from the kinds map) while telling the
    caller about a VECTOR index the catalogue never reported — the non-vector skip this
    module documents, defeated by an empty cell.

    A cell that REPEATS a name must collapse to ONE definition: two identical definitions
    would issue the same `DROP` twice; the result of that second `DROP` is UNMEASURED — no
    artifact in this repo records it — so it must simply not be issued.
    ``same_index_definitions`` is set-based, so the read-back could not catch it either.
    """
    for cell in ("", " ", b""):
        rows = [["Point", cell, "{age: [RANGE]}", "{}", "english", "[]", "NODE",
                 "OPERATIONAL", "{}"]]
        assert vector_index_definitions(rows, graph="g") == (), (
            f"a blank properties cell ({cell!r}) was read as a PROPERTY, so a non-vector "
            f"graph was refused for an index the catalogue never reported"
        )

    repeated = [["Point", "[embedding, embedding]", "{embedding: [VECTOR]}",
                 "{embedding: {dimension: 384}}", "english", "[]", "NODE",
                 "OPERATIONAL", "{}"]]
    definitions = vector_index_definitions(repeated, graph="g")
    assert len(definitions) == 1, (
        f"a repeated property yielded {len(definitions)} identical definitions, so the "
        f"same index is dropped twice and the second DROP aborts the restore: {definitions}"
    )


def test_a_failing_re_create_does_not_abandon_the_REMAINING_indexes() -> None:
    """#8965 review: the MAIN re-create loop abandoned the rest on the first raise.

    ``_try_recreate``'s own stated rule is "a failure on the first must not abandon the
    rest, or one bad definition costs every other index on the graph" — and the main path
    raised inside the loop, so with two indexes and the FIRST ``CREATE`` refused, the second
    index was never re-created although it was a perfectly good index. The note then listed
    it as "NOT re-created", i.e. as though the engine had refused it.
    """
    engine = _FakeGraph(
        [list(_INDEXED_ROWS[0]), list(_SECOND_INDEX_ROW)], create_fails_on={"embedding"}
    )

    with pytest.raises(RuntimeError) as raised:
        engine.safe()

    assert "refused to create embedding" in str(raised.value)
    creates = [call for call in engine.calls if call.startswith("create:")]
    assert creates == ["create:embedding", "create:vec"], (
        f"the loop stopped at the first failing CREATE, so the other index was never "
        f"re-created: {engine.calls}"
    )
    notes = " ".join(getattr(raised.value, "__notes__", []))
    not_recreated = [line for line in notes.splitlines() if "NOT re-created" in line]
    assert not_recreated, f"the note does not name what is missing: {notes!r}"
    assert "embedding" in not_recreated[0]
    assert "Entity" not in not_recreated[0], (
        f"the note reports the index that WAS re-created as lost: {notes!r}"
    )


def test_an_unrecognised_index_type_refuses_instead_of_reading_as_index_free() -> None:
    """#8965 review F4: the guard FAILED OPEN on any token it did not know.

    ``if VECTOR_INDEX_TYPE in types`` skipped everything else, so an engine that renamed,
    re-cased or extended the type token made the graph read as index-FREE and BOTH entry
    points issued exactly the index-bearing RESTORE this change exists to prevent. The
    polarity is now an allowlist of known non-vector tokens.
    """
    engine = _FakeGraph(
        [
            ["Point", "[embedding]", "{embedding: [vector]}", "{}", "english", "[]",
             "NODE", "OPERATIONAL", "{}"],
        ]
    )

    with pytest.raises(IndexDefinitionUnreadable) as raised:
        engine.raw()

    assert engine.destructive_calls() == [], (
        f"an unrecognised index type was read as index-free and the restore ran: {engine.calls}"
    )
    assert "does not recognise" in str(raised.value)

    # ... and the safe path refuses it too, before anything is dropped.
    engine = _FakeGraph(
        [
            ["Point", "[embedding]", "{embedding: [GEO]}", "{}", "english", "[]",
             "NODE", "OPERATIONAL", "{}"],
        ]
    )
    with pytest.raises(IndexDefinitionUnreadable):
        engine.safe()
    assert engine.calls == ["list_indexes"]


def test_a_property_the_kinds_map_omits_refuses_instead_of_reading_as_index_free() -> None:
    """#8965 review P1: the guard read ONLY the kinds column and FAILED OPEN on the other.

    ``vector_index_definitions`` iterated the per-property TYPE cell (``row[2]``) alone, so
    a row whose ``properties`` column (``row[1]``) named a property that the kinds map
    omitted yielded NO vector property, was skipped as non-vector, and left
    ``restore_graph_key`` free to issue the index-bearing RESTORE that blocks the instance
    — the exact P0 this change exists to prevent. ``index_catalogue.indexed_field_names``
    is the repo's reader for this question and documents why BOTH columns must be read.

    Two shapes reached that skip, and both must refuse with ZERO destructive calls: a
    property absent from the kinds map, and a property whose token list is EMPTY (which
    fell through because ``unknown == ()``).
    """
    omitted = [
        ["Point", "[embedding]", "{}",
         "{embedding: {dimension: 384, similarityFunction: cosine}}",
         "english", "[]", "NODE", "OPERATIONAL", "{}"],
    ]
    empty_tokens = [
        ["Point", "[embedding]", "{embedding: []}", "{}",
         "english", "[]", "NODE", "OPERATIONAL", "{}"],
    ]

    for rows, expected in ((omitted, "no entry in the per-property TYPE cell"),
                           (empty_tokens, "EMPTY index-type list")):
        engine = _FakeGraph(rows)
        with pytest.raises(IndexDefinitionUnreadable) as raised:
            engine.raw()
        assert expected in str(raised.value), str(raised.value)
        assert engine.destructive_calls() == [], (
            f"an index the kinds map did not state was read as index-free and the "
            f"RESTORE ran: {engine.calls}"
        )

        # ... and the safe path must not fall through to the UNSAFE one: with no
        # definition it would drop nothing, re-create nothing and never verify.
        engine = _FakeGraph(rows)
        with pytest.raises(IndexDefinitionUnreadable):
            engine.safe()
        assert engine.calls == ["list_indexes"], engine.calls

    # The control: the SAME rows with the kind stated are a definition, not a refusal —
    # so the two arms above cannot pass by the guard going blind to the shape.
    stated = [
        ["Point", "[embedding]", "{embedding: [VECTOR]}",
         "{embedding: {dimension: 384, similarityFunction: cosine}}",
         "english", "[]", "NODE", "OPERATIONAL", "{}"],
    ]
    definitions = vector_index_definitions(stated, graph="g")
    assert len(definitions) == 1, definitions


def test_a_catalogue_seam_that_answers_None_refuses_rather_than_reading_as_empty() -> None:
    """#8965 review F4: a seam that could not read the catalogue must not answer "empty".

    An index-free graph answers ``[]``; ``None`` means nothing was read. Publishing "no
    index" about an unread catalogue is the same fail-open as the type-token allowlist.
    """
    engine = _FakeGraph(_INDEXED_ROWS)

    with pytest.raises(IndexDefinitionUnreadable) as raised:
        engine.raw(index_guard=lambda: None)

    assert engine.destructive_calls() == []
    assert "returned None" in str(raised.value)


def test_an_overrun_does_not_re_create_the_index_either() -> None:
    """The worker is ABANDONED at the bound and may still be running engine-side.

    Re-creating the index on that path would race a restore that is still in flight. The
    caller gets ``GraphRestoreSilentError`` and the definition, not a second engine mutation.
    """

    class _Hanging(_FakeGraph):
        def restore(self, _payload):
            import time

            time.sleep(1.0)
            return "OK"

    engine = _Hanging(_INDEXED_ROWS)

    with pytest.raises(Exception) as raised:
        engine.safe(bound_s=0.2, witness_join_s=0.1, recovery_probe_s=0.0)

    assert "GraphRestore" in type(raised.value).__name__, (
        f"an overrun raised {type(raised.value).__name__}, not a typed restore error"
    )
    creates = [call for call in engine.calls if call.startswith("create:")]
    assert creates == [], (
        f"the index was re-created while the restore may still be running: {engine.calls}"
    )


# ---------------------------------------------------------------------------------
# 10a-10d: the CYCLE-2 findings — an unverified read, a false "LOST", an option
# reorder, and an error that pointed at an object the caller never receives
# ---------------------------------------------------------------------------------


def test_a_verification_read_that_fails_does_not_claim_nothing_was_touched() -> None:
    """#8965 review cycle 2, F1: the one unguarded post-restore read.

    Every other arm of the safe path attaches a note; this read did not. By the time it runs
    the graph key has been deleted, restored and the index re-created — so an
    ``IndexDefinitionUnreadable`` escaping from here told the caller "NOTHING HAS BEEN
    TOUCHED" and advised a plain restore, which is false and is advice to issue exactly the
    index-bearing RESTORE this path exists to prevent.
    """
    # call 1 = the pre-drop read, 2 = the post-drop re-read, 3 = the inner guard, 4 = verify
    engine = _FakeGraph(_INDEXED_ROWS, fail_list_on={4})

    with pytest.raises(IndexRestoreUnverifiedError) as raised:
        engine.safe()

    message = str(raised.value)
    assert "WAS restored" in message and "were re-created" in message, (
        f"the error does not say the graph and index are in place: {message}"
    )
    assert "NOTHING HAS BEEN TOUCHED" not in message, (
        f"the error claims nothing was touched about a graph that was restored: {message}"
    )
    assert "index_guard=None" not in message, (
        f"the error advises a plain (index-bearing) restore: {message}"
    )
    assert "CREATE VECTOR INDEX" in message, (
        f"the error carries no statement to act on: {message}"
    )
    # It must not raise a mismatch either: nothing measured a difference.
    assert not isinstance(raised.value, IndexRestoreMismatchError)


def test_the_post_drop_re_read_failing_is_not_a_silent_loss() -> None:
    """#8965 review (P1): the read that PROVES the drop took was the one unguarded arm.

    Call order in ``restore_graph_key_safe``: 1 = the pre-drop read, **2 = the post-drop
    re-read**, 3 = the inner guard, 4 = verify. Read 2 runs after EVERY ``drop_index()``, so
    a transport blip there — or the ``IndexDefinitionUnreadable`` whose text says "NOTHING
    HAS BEEN TOUCHED" — escaped with the index DROPPED, no re-create attempt, no note and no
    statement the caller could act on. A retry then read an index-free catalogue, dropped
    nothing, and returned successfully without the index: a silent, permanent loss.

    Pre-fix: bare ``ConnectionError``, ``create:`` never issued, ``__notes__`` absent.
    """
    engine = _FakeGraph(_INDEXED_ROWS, fail_list_on={2})

    with pytest.raises(IndexDropUnverifiedError) as raised:
        engine.safe()

    assert "dump" not in engine.calls, (
        f"the DUMP ran although whether the drop took was never read: {engine.calls}"
    )
    creates = [call for call in engine.calls if call.startswith("create:")]
    assert creates == ["create:embedding"], (
        f"the index that WAS dropped was not put back although the graph key was never "
        f"touched: {engine.calls}"
    )
    message = str(raised.value)
    assert "NOTHING HAS BEEN TOUCHED" not in message, (
        f"the error claims nothing was touched about a graph whose index was dropped: {message}"
    )
    assert "index_guard=None" not in message, (
        f"the error advises the index-bearing restore this path exists to prevent: {message}"
    )
    assert "embedding" in message and "CREATE VECTOR INDEX" in message, (
        f"the error names neither the dropped index nor a statement to restore it: {message}"
    )
    assert "NOT KNOWN" in message, (
        f"the error presents an unmeasured outcome as a measured one: {message}"
    )
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "NEVER deleted" in notes, (
        f"the note does not say the graph key survived: {notes!r}"
    )


def test_a_failed_INNER_guard_re_read_is_not_reported_as_nothing_touched() -> None:
    """The guard's own re-read is the same read one step later (#8965 review).

    With the drop taken, call 3 is ``restore_graph_key``'s ``index_guard`` read. A failure
    there used to escape as ``IndexDefinitionUnreadable`` — "NOTHING HAS BEEN TOUCHED …
    restore with ``index_guard=None``" — over a graph whose index had just been dropped.
    The recovery arm did put the index back, so the state was right and only the account of
    it was wrong; the caller was still told to issue the index-bearing restore.
    """
    engine = _FakeGraph(_INDEXED_ROWS, fail_list_on={3})

    with pytest.raises(IndexDropUnverifiedError) as raised:
        engine.safe()

    assert "dump" not in engine.calls, f"the DUMP ran on an unproven guard read: {engine.calls}"
    assert "create:embedding" in engine.calls, (
        f"the dropped index was not put back on the guard arm: {engine.calls}"
    )
    message = str(raised.value)
    assert "NOTHING HAS BEEN TOUCHED" not in message
    assert "index_guard=None" not in message
    assert "CREATE VECTOR INDEX" in message


def test_an_unindexed_graph_s_guard_read_failure_says_nothing_was_dropped() -> None:
    """No index means NO drop, so "NOTHING HAS BEEN TOUCHED" is the true statement.

    The translation to ``IndexDropUnverifiedError`` exists because the ``DROP`` loop has
    already run; claiming indexes "were DROPPED" over an index-free graph would be a new
    false statement — the class the translation exists to remove.
    """
    engine = _FakeGraph(_UNINDEXED_ROWS, fail_list_on={2})

    with pytest.raises(ConnectionError):
        engine.safe()

    assert "dump" not in engine.calls, f"the DUMP ran on an unread catalogue: {engine.calls}"
    assert not [call for call in engine.calls if call.startswith("create:")], (
        f"an index was 'put back' on a graph that carries none: {engine.calls}"
    )


def test_a_catalogue_that_cannot_be_re_read_says_UNKNOWN_not_LOST() -> None:
    """#8965 review cycle 2, F3: a failed read made the SURVIVOR look lost.

    ``_missing_from_catalogue`` returned ALL of ``expected`` when the read failed, so the
    drop-incomplete exception listed the same index as "still in the catalogue" (in
    ``remaining``) and as "WAS LOST" (in ``failed``), and handed the caller a CREATE the
    engine would refuse because the index is still there.
    """
    # calls: 1 = pre-drop, 2 = the post-drop re-read that says it survived, 3 = the
    # recovery read, which fails
    engine = _FakeGraph(_INDEXED_ROWS, drop_works=False, fail_list_on={3})

    with pytest.raises(IndexDropIncompleteError) as raised:
        engine.safe()

    error = raised.value
    assert error.unverified is True
    assert "NOT KNOWN" in str(error), (
        f"the error claims a measured loss it never measured: {error}"
    )
    assert "AN INDEX WAS LOST" not in str(error)
    # The survivor is named once, as surviving — never as lost.
    assert [definition.property for definition in error.failed] == []
    assert "dump" not in engine.calls


def test_an_option_reorder_is_not_a_mismatch() -> None:
    """#8965 review cycle 2, F4: ``signature()`` compared options in CATALOGUE ORDER.

    An engine that echoes the same options in a different order re-created the same index;
    an order-sensitive comparison raised ``IndexRestoreMismatchError`` over a perfectly good
    restore (and told the operator to "fix the index").
    """
    engine = _FakeGraph(
        _INDEXED_ROWS,
        echo=lambda definition: tuple(reversed(definition.options)),
    )

    result = engine.safe()

    assert result.indexes.verified is True, (
        "a re-ordered option map was treated as a different index"
    )


def test_a_mismatch_error_carries_the_STATEMENTS_and_no_dangling_pointer() -> None:
    """#8965 review cycle 2, F4: the message pointed at ``IndexRestoreReport.definitions``.

    That report is the RETURN VALUE of ``restore_graph_key_safe``, and this raise replaces
    it — so the caller was pointed at an object it never receives, with no statement to fix
    the index by hand.
    """
    engine = _FakeGraph(
        _INDEXED_ROWS,
        echo=lambda _definition: (
            ("dimension", 384),
            ("similarityFunction", "euclidean"),
            ("M", 16),
        ),
    )

    with pytest.raises(IndexRestoreMismatchError) as raised:
        engine.safe()

    message = str(raised.value)
    assert "IndexRestoreReport" not in message, (
        f"the error still points at a report this path never returns: {message}"
    )
    assert "CREATE VECTOR INDEX" in message, (
        f"the error carries no CREATE for the index that is missing: {message}"
    )
    assert "M: 8" in message and "efConstruction: 64" in message, (
        f"the expected definition is not in the error: {message}"
    )


# ---------------------------------------------------------------------------------
# 11-13: the definition parser, the bound, and the documented contract
# ---------------------------------------------------------------------------------


def test_the_parser_reads_a_relationship_vector_index_and_quoted_options() -> None:
    """The DROP/CREATE forms differ per entity type, and a quoted value must survive.

    A node pattern (``(p:Label)``) emitted for a ``RELATIONSHIP`` index is a ``CREATE`` the
    engine refuses — and for the DROP that failure mode is silent in the worst way: the
    index stays, the restore is index-bearing, and the safe path looks like it ran.
    """
    rows = [
        ["REL", "[embedding]", "{embedding: [VECTOR]}",
         "{embedding: {dimension: 8, similarityFunction: cosine, note: 'a, b'}}",
         "english", "[]", "RELATIONSHIP", "OPERATIONAL", "{}"],
    ]

    definition = vector_index_definitions(rows, graph="g")[0]

    assert definition.entity_type == "RELATIONSHIP"
    assert definition.drop_cypher() == "DROP VECTOR INDEX FOR ()-[p:REL]-() ON (p.embedding)"
    assert definition.create_cypher().startswith(
        "CREATE VECTOR INDEX FOR ()-[p:REL]-() ON (p.embedding) OPTIONS {"
    )
    # the comma inside a quoted value is NOT a separator
    assert "note: 'a, b'" in definition.create_cypher(), definition.create_cypher()


def test_the_module_bound_is_below_the_live_lane_s_socket_timeout() -> None:
    """The default bound must stay distinguishable from a socket timeout.

    ``tests/test_7846_restore_doc_and_bounds.py`` pins the LIVE lane's relationship
    (``_RESTORE_BOUND_S`` < ``_RESTORE_TIMEOUT_S``). This pins the MODULE default that
    ``restore_graph_key_safe`` now uses, so a caller that never passes ``bound_s`` inherits a
    bound that is still below the socket a live lane gives it — if the two were equal, the
    socket would raise at the same instant the driver gave up and "the restore overran its
    bound" could not be told from "the socket timed out".
    """
    assert 0 < DEFAULT_RESTORE_BOUND_S < live._RESTORE_TIMEOUT_S, (
        f"the module's default bound ({DEFAULT_RESTORE_BOUND_S}s) must stay below the live "
        f"lane's socket timeout ({live._RESTORE_TIMEOUT_S}s)"
    )
    assert live._RESTORE_BOUND_S <= live._RESTORE_TIMEOUT_S


def test_the_module_states_both_entries_and_which_one_refuses() -> None:
    """A caller reads the module docstring before choosing a function.

    Two entry points with opposite behaviour on an indexed graph, and the reason the raw one
    REFUSES instead of routing (it holds no ``create_index`` step, so it cannot undo what the
    restore costs), have to be on the page the caller lands on.
    """
    from tortoise import graph_key_restore as module

    doc = " ".join((module.__doc__ or "").split())
    assert "restore_graph_key_safe" in doc, (
        "the module docstring does not name the index-safe entry point"
    )
    assert "read the index definition" in doc and "re-CREATE" in doc, (
        "the module docstring does not state the safe order (read → drop → restore → re-create)"
    )
    assert "REFUSES an index-bearing graph" in doc, (
        "the module docstring does not say the raw driver refuses"
    )
    assert "because it holds no ``create_index`` step" in doc, (
        "the module docstring does not say WHY the raw driver refuses instead of routing"
    )
    safe_doc = " ".join((restore_graph_key_safe.__doc__ or "").split())
    assert "does NOT" in safe_doc or "ONLY after a restore that RETURNED" in safe_doc, (
        "restore_graph_key_safe's contract does not say when re-creation is skipped"
    )


def test_the_definition_identity_is_the_whole_definition() -> None:
    """``same_index_definitions`` is what ``verified`` rests on — pin its one rule."""
    rows = [list(row) for row in _INDEXED_ROWS]
    definition = vector_index_definitions(rows, graph="g")[0]

    identical, missing, extra = same_index_definitions([definition], [definition])
    assert identical is True
    assert missing == () and extra == ()

    degraded = [
        _catalogue_row(definition, options=(("dimension", 384), ("similarityFunction", "euclidean")))
    ]
    identical, missing, extra = same_index_definitions(
        [definition], vector_index_definitions(degraded, graph="g")
    )
    assert identical is False
    # Both halves are reported: the definition that was read is GONE from the catalogue
    # (`missing`) and one that was never asked for took its place (`extra`). Either alone
    # would be a different fact — "the index vanished" vs "a different index exists" — and
    # the error text names both.
    assert missing, "a degraded re-create was not reported as a MISSING definition"
    assert extra, "the index that replaced it was not reported"
