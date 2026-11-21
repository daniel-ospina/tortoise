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

#: Capability words a reader would have to need for the shape to stay resident.
#: The vocabulary is the audit's own (`#13083` §2): the four capabilities only a
#: graph engine provides, plus EP state. The PREDICATE lives in the tool, not
#: here — a check whose vocabulary the checked module does not own is a second
#: definition that drifts. This tuple is the EXPECTED set, pinned against the
#: tool's own in `test_the_capability_vocabulary_has_one_owner`, so a narrowing
#: edit to the tool cannot silently loosen the census pin.
GRAPH_CAPABILITIES = ("traversal", "adjacency", "fulltext", "vector", "ep walk",
                      "variable-length")


def _tool():
    """The verb, imported by path (`tools/` is not a package)."""
    spec = importlib.util.spec_from_file_location("graphevent_home", TOOL)
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

    ⛔ `min_occurrences` is what makes the pin able to catch a LOSS: two rows in
    `event_store.py` share one statement text (the dedupe probe and
    `_event_row_exists`), as do the two whole-graph export scans in
    `hosted_backup.py`. A single-occurrence check would let one survivor satisfy
    both rows, so deleting the other would go unnoticed.
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

    # The control: the predicate must CATCH a capability that lives only in the
    # snippet, under a class that claims none. If this stops failing to be clean,
    # the pin above has gone vacuous again.
    smuggled = {
        "file": "tortoise/event_store.py", "line": 992, "reader": "walk_out",
        "needs": "walks outward from each event to the nodes it touched",
        "why": "feed enrichment", "class": "maintenance",
        "snippets": ("MATCH (e:GraphEvent)-[*1..2]->(n) RETURN n",),
    }
    assert module.graph_capability(smuggled), (
        "the capability pin only reads prose, so a traversal filed as "
        "`maintenance` passes it — the census can go false with every pin green")


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
    direction that produces a false `covered`. The reader pages with `>=` and
    dedupes, so all three rows survive a two-row page.
    """
    module = _tool()
    rows = [
        (1, "PointAdded", "e1", '{"id": "a"}'),
        (2, "PointAdded", "e2", '{"id": "b"}'),
        (2, "PointAdded", "e3", '{"id": "c"}'),
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
    """The verb an operator points at a TENANT must not mint it either.

    `residency` was guarded and `superset` was not (review round 2, P1): the
    guard sat in one verb, so the one whose verdict gates a re-home was the one
    that would create an empty graph from a `--graph` typo and then compare it.
    """
    module = _tool()
    db = _StubDB(_StubGraph([]))
    monkeypatch.setattr(module, "_graph_connection", lambda uri: db)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
    monkeypatch.setattr(module, "_record_connection",
                        lambda dsn, org: (_ for _ in ()).throw(
                            AssertionError("the record must not be read for a "
                                           "graph that does not exist")))

    class _NoRecord:
        def cursor(self):
            return type("_C", (), {"execute": staticmethod(lambda *a: None),
                                   "fetchall": staticmethod(lambda: [])})()

    monkeypatch.setattr(module, "_record_connection", lambda dsn, org: _NoRecord())
    assert module.main(["superset", "--graph", "g_never_existed", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err
    assert db.selected == [], "superset bound a graph the server does not hold"


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
    strings before numbers, so a numeric cursor matches no row and the walk ends
    early with a page that looks like the end of the feed. The stub's `>`
    comparison would retrieve it, so this arm drives the count guard directly by
    making the walk's own pages stop short.
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

    def _refuse(*_a, **_k):
        raise RuntimeError("the store refused the read")

    monkeypatch.setattr(module, "residency", _refuse)
    assert module.main(["residency", "--graph", "g_x"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    monkeypatch.setattr(module, "_record_connection", _refuse)
    assert module.main(["superset", "--graph", "g_x", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    def _no_driver(*_a, **_k):
        raise module.RecordUnavailable("psycopg2 is not installed")

    monkeypatch.setattr(module, "_record_connection", _no_driver)
    assert module.main(["superset", "--graph", "g_x", "--org-id", "o",
                        "--graph-id", "g"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err


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
