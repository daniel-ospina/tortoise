"""The ONE definition of "wait until the FalkorDB index catalogue has settled" (#10807).

WHY THIS MODULE EXISTS
----------------------
FalkorDB retires a dropped index field ASYNCHRONOUSLY: a label's fields live in
ONE index, so dropping one REBUILDS that whole index on the engine's indexer
thread. Until it finishes, a `CALL db.indexes()` read still reports the PRE-drop
definition — the retired row keeps naming the dropped field, and the new row's
status reads `[Indexing] N/M: UNDER CONSTRUCTION` (the procedure yields a
`status` column; docs.falkordb.com/cypher/indexing). Measured on an idle lane:
0.3 s to converge. On a LOADED CI runner the same window outlived the read, and
fixtures in different files each reported a drop that had LANDED as one that had
not:

  * `test (g)`, run 41817275671 — `tests/test_9079_handle_write_authority.py`
    dropped `:Point(pointKind)` and its own vacuity guard refused the good
    fixture: "the fixture could not drop the Point range index".
  * `test (b)`, run 41868531069 — `tests/test_search_engine.py` dropped
    `:Object(name)` FULLTEXT and `assert proj._schema_is_current() is False` got
    `True`, because the catalogue still carried the FULLTEXT kind.

ONE definition, because that is one race. Two copies would drift — and the
divergence would be invisible, since each copy only fails on a loaded runner.

⛔ NOT in `tests/_live_utils.py`: that module is a declared `SHARED_MODULES`
member (`tools/ci_selection.py`) because the docker-lane port seam is read by
every surface, so a one-line edit there runs the FULL CI matrix. This module's
consumers are on `core` (a `tests/`-prefixed change falls through to
`core`), so it stays narrow — the same reasoning as `tests/_html_links.py`.
"""
from __future__ import annotations

import time

#: A runaway guard, not an expected wait: the measured window is sub-second
#: idle and its CI overrun was still seconds. Deliberately generous, because
#: expiring early re-introduces exactly the false red this exists to remove.
INDEX_SETTLE_TIMEOUT_S = 120.0
INDEX_SETTLE_POLL_S = 0.1


def index_rows_after_settle(proj, *, until=None,
                            timeout: float = INDEX_SETTLE_TIMEOUT_S,
                            poll_s: float = INDEX_SETTLE_POLL_S) -> list:
    """`CALL db.indexes()` rows, once the catalogue has SETTLED.

    Settled means BOTH: no row reports a rebuild in flight (no cell reads
    ``UNDER CONSTRUCTION``), and — when the caller passes ``until(rows)`` — the
    caller's own precondition is observable. ``until`` exists because a read
    taken BEFORE the engine applied the drop is internally consistent: no
    rebuild status, the idle ``OPERATIONAL`` one, the pre-drop definition. Only
    the caller knows what that read has to stop saying.

    ⛔ A BOUND THAT EXPIRES RETURNS THE LAST READ, never a tolerant pass. The
    callers' assertions are unchanged, so a drop that genuinely cannot happen —
    or a schema that genuinely never converges — still reds loudly on the
    mid-flight catalogue instead of passing vacuously.

    The read is `query`, the same door ``_schema_is_current`` reads through: a
    `ro_query` on a replica lane could answer from a lagging node and spin here.
    """
    deadline = time.monotonic() + timeout
    while True:
        rows = proj.g.query("CALL db.indexes()").result_set
        rebuilding = any(
            "UNDER CONSTRUCTION" in str(cell) for row in rows for cell in row)
        settled = not rebuilding and (until is None or bool(until(rows)))
        if settled or time.monotonic() >= deadline:
            return rows
        time.sleep(poll_s)


def index_cells(rows) -> set[str]:
    """The flattened catalogue cells a fixture asserts against.

    One string per COLUMN, not per leaf: the list/dict columns stringify whole,
    which is what `not any("pointKind" in c for c in cells)` relies on to find a
    retired field inside the kinds map.
    """
    return {str(cell) for row in rows for cell in row}


def _row_reports_kind(row, field: str, kind: str) -> bool | None:
    """Does this catalogue row report ``field`` under ``kind``?

    ``None`` means the row CANNOT ANSWER — an absent/short row, or a kinds map
    that is not a map. Kept as ONE reader because the two predicates below are
    that same question with opposite polarity on the unanswerable case ("keep
    waiting" for a create, "do not certify" for a drop); two copies of the
    kind-extraction would drift silently — the divergence this module's own
    header forbids.
    """
    if not row or len(row) < 3 or not isinstance(row[2], dict):
        return None
    kinds = row[2].get(field) or ()
    if isinstance(kinds, str):
        kinds = [kinds]
    return kind.upper() in {str(k).upper() for k in kinds}


#: The consumers this module documents, and whose "wait" call sites the
#: structural pin therefore REQUIRES (`test_every_consumer_passes_the_caller_
#: precondition`): a rename or a dropped wait in any of them keeps a count-based
#: floor green, so the floor names modules rather than a number. `test_indexes.py`
#: joined at #12856 — its index-requirement test read the catalogue straight
#: after the DDL and red on a LANDED drop.
_DOCUMENTED_CONSUMERS = {
    "test_9079_handle_write_authority.py",
    "test_search_engine.py",
    "test_11682_quote_indexed.py",
    "test_indexes.py",
}


#: The (module, function) pairs whose settle call the structural pin REQUIRES
#: (`tests/test_10807_index_settle.py::test_every_consumer_passes_the_caller_
#: precondition`). A module-level key is too coarse: measured at #12856's review,
#: deleting BOTH waits inside `test_event_id_index_is_required_so_an_existing_
#: graph_gets_it` left the module key satisfied by its five siblings — the pin
#: for that very red went vacuous while staying green. A method is spelled
#: `Class.method`, which is how the scan reports it.
#:
#: ⛔ THIS SET IS THE WHOLE ARMAMENT OF EVERY ``_DOCUMENTED_CONSUMERS`` MODULE,
#: not a sample of it: the pin asserts set EQUALITY over those modules, so an
#: armed wait that is missing here reds instead of sitting outside the floor.
#: Measured at #12856's review cycle 5 — with a `required <= armed` floor, SIX of
#: the ELEVEN armed functions were undeclared across those four modules and each
#: could have lost its wait with the pin green. Declaring one and waiting for the
#: next review to find the next one is the whack-a-mole this closes.
#:
#: A HELPER counts: `test_9079_handle_write_authority.
#: _await_point_indexes` is where that module's wait lives, so it is the pair a
#: removal would touch.
_DOCUMENTED_SITES = {
    # #10807's originals
    ("test_9079_handle_write_authority.py",
     "test_schema_ddl_survives_the_armed_guard"),
    ("test_9079_handle_write_authority.py", "_await_point_indexes"),
    ("test_search_engine.py",
     "TestR2OrUnionAndSearchKeys.test_event_migration_does_not_drop_an_"
     "already_correct_index"),
    ("test_search_engine.py",
     "TestR2OrUnionAndSearchKeys.test_fts_leg_trace_index_missing_live"),
    # #11682's upgrade/pre-existing arms
    ("test_11682_quote_indexed.py",
     "test_a_pre_11682_two_field_index_is_upgraded_to_carry_the_quote"),
    ("test_11682_quote_indexed.py",
     "test_a_pre_existing_array_quote_is_flattened_by_the_sweep"),
    # #12856 — the red this list gained a consumer for, and its three siblings
    # that the same diff arms
    ("test_indexes.py",
     "test_event_id_index_is_required_so_an_existing_graph_gets_it"),
    ("test_indexes.py", "test_schema_probe_is_true_only_on_a_complete_schema"),
    ("test_indexes.py", "test_schema_probe_rejects_the_boolean_is_operator_index"),
    ("test_indexes.py", "test_schema_probe_is_per_graph_not_per_process"),
}


def index_kind_present(rows, label: str, field: str, kind: str) -> bool:
    """Does SOME catalogue row for ``label`` report ``field`` as ``kind`` yet?

    The positive counterpart of :func:`index_kind_absent`, and it exists for the
    same reason: a KIND-BLIND predicate can release a wait before the index its
    caller asserts about exists.

    ⛔ WHAT THIS BUYS — KIND EXACTNESS AND FAIL-CLOSEDNESS, NOT CREATE LATENCY.
    A create is NOT deferred the way a drop is: its DEFINITION (this kinds map)
    is registered synchronously, and only the POPULATION is queued
    (``Indexer_PopulateIndex`` -> ``INDEXER_IDX_POPULATE``; the row then reads
    ``[Indexing] N/M: UNDER CONSTRUCTION`` with the kinds map ALREADY present).
    Measured: an immediate catalogue read 5.7 ms after ``CREATE INDEX FOR
    (n:Point) ON (n.lastDreamedAt)`` already names ``lastDreamedAt: [RANGE]``
    with status ``OPERATIONAL``. So the wait that actually removes lateness is
    the one on the DROP side, and ``index_rows_after_settle``'s
    ``UNDER CONSTRUCTION`` leg is what covers the populate window. What THIS
    predicate adds is that a field already present under a DIFFERENT kind must
    not release the wait (a kind-blind predicate would), and that a row which
    cannot be read keeps the wait closed.

    A row whose kinds map is MISSING answers False: a catalogue that cannot
    prove the create must not certify it, so the caller keeps waiting (bounded,
    fail-closed) instead of asserting about a read that never answered.
    """
    for row in rows:
        if not row or str(row[0]) != label:
            continue
        if _row_reports_kind(row, field, kind) is True:
            return True
    return False


def index_kind_absent(rows, label: str, field: str, kind: str) -> bool:
    """Does NO catalogue row for ``label`` still report ``field`` as ``kind``?

    The "is the drop VISIBLE yet" question a fixture asks after it drops an
    index — read from the ``CALL db.indexes()`` kinds map (row[2]), which is the
    same column ``FalkorProjection._schema_is_current`` reads, so the two cannot
    disagree about what the catalogue says.

    A row whose kinds map is MISSING answers False: a catalogue that cannot
    prove the drop must not certify it, so the caller waits (bounded,
    fail-closed) instead of asserting about a read that never answered.
    """
    for row in rows:
        if not row or str(row[0]) != label:
            continue
        reports = _row_reports_kind(row, field, kind)
        if reports is None or reports:
            return False
    return True


#: What `test (g)` drops before it reads the catalogue: the retired
#: `:Point(pointKind)` range field, and the label's legacy fulltext index
#: (`content` + `search_keys` — the fields that index carried when this wait was
#: written; #11682 later added `quote`). The two vacuity guards at the call site
#: assert exactly these are gone, so the wait must assert the same thing.
#:
#: ⛔ A SUBSET is the right statement, not a stale one: `test (g)` drops the
#: WHOLE Point fulltext index, and this predicate only asks whether the drop is
#: visible. Please do not "sync" it to the declared field set — a fixture that
#: models the pre-drop row (content + search_keys) would then read as
#: "drop visible" and the wait would certify before the engine applied it.
POINT_RANGE_FIELD = "pointKind"
POINT_FULLTEXT_FIELDS = ("content", "search_keys")


def point_index_drops_visible(rows) -> bool:
    """Are BOTH `test (g)` drops VISIBLE in ``rows`` yet?

    `index_kind_absent` answers one field/kind question; this is the pair the
    write-authority fixture needs, written once so the wait and the two guards
    below it cannot disagree about what "dropped" means.

    ⛔ Why the call site may not rely on quiescence alone: `DROP INDEX` returns
    before the engine's indexer thread applies it, and in that window the
    catalogue is internally consistent — pre-drop definition, no
    ``UNDER CONSTRUCTION``, idle ``OPERATIONAL`` — so a quiescence-only wait
    hands that read straight back and the fixture refuses a LANDED drop as if
    the drop had failed (the false red this module exists to remove).
    """
    cells = index_cells(rows)
    return (
        not any(POINT_RANGE_FIELD in cell for cell in cells)
        and not any(all(f in cell for f in POINT_FULLTEXT_FIELDS) for cell in cells)
    )
