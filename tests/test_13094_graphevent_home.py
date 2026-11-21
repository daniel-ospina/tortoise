"""#13094 — the change feed's readers, and the two things that block its move.

WHAT THIS FILE PINS, and what each pin would be FALSE without:

1. **The reader census is an ARTIFACT, not prose.** `tools/graphevent_home.READERS`
   is the per-reader table `#13094` ships; every entry's statements are asserted
   to still appear in the file it names. Without that, the table is an evidence
   document that drifts one merge behind the code it claims to describe — the
   class `#13113` filed an extractor for.

2. **No reader needs a graph capability.** The census's verdict is
   ``record_only``-shaped: every reader is a key lookup, an ordered range scan, an
   aggregate or a predicate delete. The pin is a VOCABULARY test over the table,
   so a future reader that genuinely needs a traversal cannot be added to the
   table as "maintenance" without this going red.

3. **`journal_event` is not the change feed's home, and the reason is a producer
   contract — not a missing column.** Two independent, EXECUTED refutations:

   * a payload-only emit writes a `:GraphEvent` row and **no journal row at all**
     (`_emit_event` returns before the JSONL half when it has no ``point``/``id``/
     ``turns``), so the record's stream is short by construction for the
     hard-delete lane — the shape `#13083` §6.3 named and this test EXECUTES;
   * the two stores' `seq`s are different coordinates (`:GraphEvent.seq` is the
     change feed's own allocator, `journal_event.seq` is the replay journal's
     ordinal, `#12894`), so no join on `seq` can be a cursor.

4. **The tier mechanism cannot express this move — and `#4664` is why.**
   `cache_projection.read_record_view` builds its SELECT from `NODE_TABLES` for
   EVERY label it is asked for, so "give it a `NODE_TABLES` row, then tier it"
   would make a record-sourced rebuild PROJECT `:GraphEvent` back into the graph:
   the exact resurrection `#4664` says must not happen. The test patches the two
   registries and shows the reader reaching for the table (fail-open if the
   projection were complete) and then refusing on an UNCLASSIFIED column — the
   placement table is the second lock, and a third is the key contract
   (`node_identity`'s CHECK has no `GraphEvent`, and `seq` is not a string key).

5. **A read that could not run produces no verdict.** The verb's exit codes carry
   that: 2 is "could not run", never "covered".

Nothing here changes product behaviour. `#13094`'s move is a STORE re-home (the
change feed is not a projection of the record), which is filed as its own work
item; this file makes the blocking facts executable so the next lane starts from
the measurement instead of re-deriving it.
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
#: graph engine provides, plus EP state.
GRAPH_CAPABILITIES = ("traversal", "adjacency", "fulltext", "vector", "EP walk",
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
    """
    module = _tool()
    missing = []
    for reader in module.READERS:
        source = _collapse((REPO_ROOT / reader["file"]).read_text(encoding="utf-8"))
        for snippet in reader["snippets"]:
            if _collapse(snippet) not in source:
                missing.append(f"{reader['file']}:{reader['line']} "
                               f"{reader['reader']} — {snippet!r}")
    assert not missing, (
        "the reader census no longer resolves — re-derive the row(s) (and the "
        "`line` addresses) at the CURRENT head, never by renumbering:\n  "
        + "\n  ".join(missing))


def test_no_reader_of_the_change_feed_needs_a_graph_capability():
    """The verdict the whole issue rests on, as a check on the table itself.

    ⛔ The pin is not "the table says so" — it is that NO row's `needs` mentions
    a graph capability. A future reader that needs one cannot be filed as
    `maintenance` without reddening this, which is what makes
    `record_only`-shaped a property of the census rather than a claim beside it.
    """
    module = _tool()
    offenders = [
        f"{r['file']}:{r['line']} {r['reader']} — {r['needs']}"
        for r in module.READERS
        if any(word.lower() in (r["needs"] + " " + r["why"]).lower()
               for word in GRAPH_CAPABILITIES)
    ]
    assert not offenders, (
        "a reader of the change feed now needs a graph capability, so the "
        "shape is NOT a record_only candidate — re-open #13094's verdict:\n  "
        + "\n  ".join(offenders))


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
    hard-delete lane uses it verbatim (`sdk.py`, "Two stores, one meaning each").
    It is payload-only: no ``point``, no ``id``, no ``turns``. So:

    * the change feed gets a row (``type`` is in ``_GRAPH_EVENT_TYPES``),
    * the JSONL journal gets NOTHING — `_emit_event` returns before the journal
      half, by contract: *"Events with NONE of the three are skipped"*.

    A re-point onto `journal_event` would therefore drop this event from every
    subscriber while the stream still looked complete. That is not an off-by-one
    cursor; it is a missing event type, which is why `#13083` §6.3 refused to
    call the move a "feed re-pointing".
    """
    from tortoise import event_store
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
        journal_rows = log.read_all()
    finally:
        sdk.close()

    assert journal_rows == [], (
        "a payload-only emit reached the journal — either the producer changed "
        "(then #13094's re-point became possible, and this pin must be "
        "re-derived) or the fixture grew a journal write it did not have")


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
        "record-sourced rebuild would re-CREATE the shape from the record view — "
        "the resurrection `#4664` establishes must not happen (a rebuild leaves "
        "the store EMPTY and carries only the watermark). The move is a STORE "
        "re-home, not a tier.")
    assert "GraphEvent" not in cp.KEY_PROPERTY, (
        "the record's key contract has no `:GraphEvent` key; `node_identity`'s "
        "CHECK restricts the labels, so a row could not satisfy the FK anyway")


def test_adding_the_shape_to_the_registries_would_project_it_into_the_graph(
        monkeypatch):
    """The consequence, executed — this is why the route above is refused.

    With the two registries patched, `read_record_view` (the ONLY view a rebuild
    materialises from) reaches straight for the table: its SELECT is derived from
    `NODE_TABLES[label]` with no per-label special case. It then REFUSES the row,
    and the refusal is the second and third lock:

    * `UnknownRecordColumn` — the placement table has not classified
      `journal_event`'s columns, so nothing may silently become a node property;
    * the key contract (`node_identity`'s CHECK, `KEY_PROPERTY`) has no
      `GraphEvent`, so even a classified row has no key to MERGE on.

    So the tier route needs THREE new declarations (a table mapping, a key
    contract, a column classification) — and the first one alone would already
    have made a rebuild re-create the shape. That is the argument for a store
    re-home, made executable.
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

    with pytest.raises(Exception) as refused:
        cp.read_record_view(conn, org_id="o", graph_id="g", labels=("GraphEvent",))

    selects = [sql for sql in conn.cursor_obj.sql
               if sql.startswith("SELECT * FROM public.")]
    assert any("public.journal_event" in sql for sql in selects), (
        "the record view must be the label-agnostic reader this test claims — "
        f"it issued {conn.cursor_obj.sql!r}")
    assert type(refused.value).__name__ in {
        "UnknownRecordColumn", "UnprojectableRecord", "UnknownRecordLabel",
        "KeyError"}, (
        "the projection accepted a `journal_event` row as a node — the placement "
        f"table is no longer the lock this test depends on ({refused.value!r})")


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
        {"type": "PointRetracted", "payload": {"id": "b"}},
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


def test_a_read_that_could_not_run_reports_no_verdict(monkeypatch, capsys):
    """Exit 2, never "covered": an unrun read is not an answer.

    Both arms (no graph URI, no record DSN) are checked, because the verb's
    worst failure mode is printing a reassuring `covered` it never measured.
    """
    module = _tool()
    for name in ("TORTOISE_DB_URI", "TORTOISE_RECORD_DSN"):
        monkeypatch.delenv(name, raising=False)

    assert module.main(["residency"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
    assert module.main(["superset", "--org-id", "o", "--graph-id", "g"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err


def test_a_store_that_refused_exits_2_and_never_1(monkeypatch, capsys):
    """A read that FAILED must not share exit 1 with "not covered".

    This is not hypothetical politeness: `main()` returns 1 for a produced
    `not_covered` verdict, so an uncaught store exception would leave Python's
    default exit status — **also 1** — and a caller gating on the exit would read
    "the record is short" from a record it never read. The two arms below are the
    graph read and the record read.
    """
    module = _tool()
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:@127.0.0.1:1/x")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://nowhere/record")

    def _refuse(*_a, **_k):
        raise RuntimeError("the store refused the read")

    monkeypatch.setattr(module, "residency", _refuse)
    assert module.main(["residency"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    monkeypatch.setattr(module, "_record_connection", _refuse)
    assert module.main(["superset", "--org-id", "o", "--graph-id", "g"]) == 2
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
    assert report["counts"] == {"serving": 1, "maintenance": 12}
    assert report["graph_capability_readers"] == []
