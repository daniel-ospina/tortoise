"""#9555 / R7 — §9.1's narrative and the raw turn text have a home in the WRITE PATH.

THE ITEM, AND THE TWO HALVES THAT WERE STILL MISSING
====================================================
`docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 places the S1 narrative *"as
text in Postgres, together with the raw agent-session transcript it was derived
from — not in the graph, not a `Document` node, not a `Point`"* (§4.1's owner
quote: *"the narrative is not something we're suggesting to store in the graph
(same as raw) but store in supabase"*). §9.4 makes the link load-bearing: *"at
answer time, send the source's VERBATIM text, not the extracted claim"*
(measured 15.9 / 22.0 pts).

`#9638` landed the HOME (`public.session_narrative`), the shape-keyed projection
and the record-only reader, and stated its own remainder. Measured at
`f86ae965b` — this file's head — that remainder was exactly two things:

1. **(b) no journal carrier.** The narrative was written to the graph by RAW
   CYPHER (`hosted_api._execute_commit_writes`, `s.story_arc=$arc`), and raw
   Cypher is not a journal record (§17.1's unjournalled-writer ledger), so a live
   capture landed `session_narrative` with its two COUNTS filled and its two TEXT
   columns NULL. A record-sourced rebuild could reproduce the rows, not the
   narrative.
2. **(c) no production caller.** `tortoise/record_read.py` existed and
   `git grep -n 'record_read import' -- tortoise/ tests/` found its own test and
   nothing else, so §9.4's fetch had a reader and no path.

WHAT THIS FILE PINS, AND THE ONE THING EACH PIN IS FOR
=====================================================
§1 the CARRIER exists in the write path, carries the right keys, and carries
   NOTHING when there is no narrative to carry. A producer that emits an empty
   record per capture is a no-op line on the journal forever.
§2 the RECORD EFFECT: the carrier's payload lands §9.1's narrative in
   `public.session_narrative` through the SHAPE-KEYED projection, the two
   carriers (narrative vs counts) cannot clobber each other in EITHER arrival
   order, and the type is dispatched by no graph fold — which is the §9.1 point,
   not an omission (§1's `_NO_PROJECTION_FOLD` membership is the declaration).
§3 the READ PATH is record-only, and the verb's 3-versus-2 split is real: no
   absence is reported until the record's OWN tenant prologue has scoped the
   connection and read the GUC back, because a tenancy-filtered read returns 0
   rows SILENTLY (§9555's G1, #9600's app role).
§4 the LIVE legs, against a real Postgres (`TEST_RECORD_DB_URI`, armed in CI by
   the `test-record-postgres` job, #9551): a journal replay produces the
   narrative + the redaction count in the record, and the record serves §9.4's
   text — through the verb, **as the app role**, which is the configuration the
   runbook mandates. ((b)'s cache-destroying leg lives in
   `tests/test_9555_narrative_cache_rebuild.py`: it needs a FalkorDB as well, and
   the armed record job provisions none.)

⛔ WHAT THIS DOES NOT CLAIM. The record's DSN is still unarmed in production
(R1, #9548), so "a live hosted capture lands the text" is proven here against a
journal the producer's own payload shape builds, not against a deployed
capture. The hosted answer path's verbatim fetch (an MCP/SDK SURFACE verb) is
deliberately NOT changed: `AGENTS.md` → *MCP/SDK Surface Approval* puts that
behind Daniel's sign-off, so this lands the operator verb
(`python -m tortoise record-text`) instead, and says so rather than implying the
surface moved.
"""
from __future__ import annotations

import ast
import json
import os
import uuid
from pathlib import Path

import pytest

from tortoise import hosted_api, record_read, record_text
from tortoise import record_projection as rp

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The narrative this file carries end to end. Deliberately prose with a
#: newline and a quote in it: a carrier that round-trips through a JSON envelope
#: and a text column must not normalise, escape or truncate it, and a
#: single-line fixture cannot tell.
NARRATIVE = (
    "The owner opened with the pricing question, then ruled that a source is\n"
    "a Source — \"not a Document node\" — and the capture followed."
)
SUMMARY = "owner rules sources are Sources"
SESSION = "sess-9555-narrative"


def _carrier(**over):
    kwargs = dict(session_id=SESSION, event_id="evt-9555", captured_at="2026-10-24T00:00:00Z")
    kwargs.update(over)
    return hosted_api._narrative_carrier(NARRATIVE, SUMMARY, **kwargs)


def _narrative_record():
    return {"type": "SessionNarrated", "ts": "2026-10-24T00:00:00Z", **_carrier()}


def _counts_record(redactions=3, turns=2):
    return {"type": "SessionRecorded", "id": SESSION, "ts": "2026-10-24T00:00:00Z",
            "capture_redactions": redactions, "turn_count": turns, "is_episodic": True}


# ── §1 the carrier in the write path ────────────────────────────────────────

def test_the_carrier_names_its_session_and_carries_the_narrative():
    """The payload the shape-keyed projection reads, key by key.

    `sessionId` is one of the two spellings the projection accepts (the commit's
    own; the capture's is `session_id`), and `story_arc` is §9.1's narrative
    under the graph's own name for it — the SAME key the raw Cypher writes one
    statement earlier, so the record and the cache state one fact.
    """
    carrier = _carrier()
    assert carrier is not None
    assert carrier["sessionId"] == SESSION
    assert carrier["story_arc"] == NARRATIVE
    assert carrier["summary"] == SUMMARY
    assert carrier["id"] == "evt-9555"


@pytest.mark.parametrize("empty", ["", "   ", "\n\t ", None])
def test_an_empty_narrative_EMITS_NOTHING(empty):
    """`story_arc` is `""` for a capture whose distillation produced no narrative.

    Emitting then would journal a record whose entire payload the narrative home
    REFUSES (a blank narrative is not §9.1 prose) — one no-op line per such
    commit, forever, plus a `journal_event` row claiming a capture's narrative
    that does not exist.
    """
    assert hosted_api._narrative_carrier(
        empty, SUMMARY, session_id=SESSION, event_id="e", captured_at="t") is None


def test_the_commit_path_emits_the_carrier_and_writes_no_narrative_onto_a_node():
    """§9.1/D1's placement, pinned structurally (#9555 / #10000).

    The narrative has exactly ONE home — `public.session_narrative`, written by
    the `SessionNarrated` carrier — and the graph holds NONE of it. This pin is
    what reds if the prose comes back onto a node: it walks the commit write
    phase and refuses a `story_arc` SET on either identity anchor the phase
    mints (the transcript `:Source`, the AgentSession `:Event`).

    ⛔ THIS USED TO PIN LINE ORDER ("the carrier is emitted AFTER the raw
    `s.story_arc=$arc` MERGE it describes"), because §17.1's apply-first lane
    requires a record to state a write that already landed. #10000 DELETED that
    write — §9.1/D1 forbids it — so the ordering premise is gone and the
    invariant that remains is the placement itself: the carrier is still
    emitted, and no narrative text reaches a node. Re-derived, not deleted.
    """
    src = (REPO_ROOT / "tortoise" / "hosted_api.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_execute_commit_writes"),
              None)
    assert fn is not None, "the commit write phase is gone — re-measure #9555"
    narrative_node_writes = [
        (n.lineno, n.value) for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "story_arc" in n.value]
    assert not narrative_node_writes, (
        "the commit write phase writes `story_arc` again at "
        f"{narrative_node_writes} — §9.1/D1 places the S1 narrative in Postgres "
        "ONLY (\"not in the graph, not a `Document` node, not a `Point`\"), and "
        "its one home is `public.session_narrative` (#10000)")
    emit_line = min((n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "_emit_event"
                     and n.args
                     and isinstance(n.args[0], ast.Name)
                     and n.args[0].id == "NARRATIVE_RECORD_TYPE"), default=None)
    assert emit_line is not None, (
        "the commit path no longer emits the narrative carrier — that is "
        "exactly the #9555 (b) gap returning: the record keeps the COUNTS and "
        "loses the TEXT, and with the node write gone (#10000) the narrative "
        "would then be stored NOWHERE")


# ── §2 the record effect, through the shape-keyed projection ────────────────

def _narrative_statements(records):
    return [s for s in rp.replay_plan(records, org_id="org", graph_id="graph")
            if rp.NARRATIVE_TABLE in s.sql]


def test_the_carrier_lands_the_narrative_and_its_summary():
    stmts = _narrative_statements([_narrative_record()])
    assert len(stmts) == 1, "the secondary projection did not fire for the carrier"
    stmt = stmts[0]
    assert "INSERT INTO public.session_narrative" in stmt.sql
    assert NARRATIVE in stmt.params, "the narrative text did not reach the row"
    assert SUMMARY in stmt.params, "`summary` is projected only alongside the narrative"
    # §9.1: the narrative has no graph home, so the type materialises NO node —
    # declared, not merely unobserved.
    assert not any("node_identity" in s.sql for s in
                   rp.replay_plan([_narrative_record()], org_id="o", graph_id="g"))


def _set_clause(stmt) -> str:
    """The COLUMN half of one upsert's `DO UPDATE SET` — the columns it OWNS.

    The `props` merge is cut off: every upsert names the table, so a bare
    substring search for a column name is answered by the table name on the
    statement that does NOT own it (a false pass of the exact clobber test this
    helper exists for).
    """
    tail = stmt.sql.split("DO UPDATE SET", 1)[1]
    return tail.split(", props =", 1)[0]


def test_the_two_carriers_cannot_clobber_each_other_in_EITHER_order():
    """The narrative record owns the TEXT, `SessionRecorded` owns the COUNTS.

    Each upsert names only its own columns, so a replay's result cannot depend
    on arrival order. The failure this prevents is silent and total: a
    whole-column write would erase whichever half landed first, and the reader
    would answer "no narrative" for a session the record holds one for.
    """
    for order in ([_narrative_record(), _counts_record()],
                  [_counts_record(), _narrative_record()]):
        stmts = _narrative_statements(order)
        assert len(stmts) == 2, f"{len(stmts)} statement(s) for the two carriers"
        owns_text = [s for s in stmts
                     if "narrative = EXCLUDED.narrative" in _set_clause(s)]
        owns_counts = [s for s in stmts
                       if "capture_redactions = EXCLUDED.capture_redactions"
                       in _set_clause(s)]
        assert len(owns_text) == 1, "exactly one carrier owns the narrative column"
        assert len(owns_counts) == 1, "exactly one carrier owns the count columns"
        # The clobber test itself: neither upsert may touch the other's half.
        assert "capture_redactions" not in _set_clause(owns_text[0])
        assert "narrative" not in _set_clause(owns_counts[0])
        assert "turn_count" not in _set_clause(owns_text[0])


def test_a_count_the_column_cannot_hold_writes_NO_row_and_no_raise():
    """#9264's shape refusal, one table over — and it must stay fail-open.

    `session.capture_redactions` was `jsonb` (an object with a `total`, not an
    integer), and `_NARRATIVE_INT_KEYS` are `INTEGER` here. A producer that
    still emits the object shape must not be able to FAIL the narrative home's
    write — the secondary projection is additive, so a raise here would turn it
    into a new way for a live capture to die. It is an ABSENCE instead.
    """
    unrepresentable = {"type": "SessionRecorded", "id": SESSION,
                       "capture_redactions": {"total": 7}, "turn_count": 2}
    # The representable half still lands (`turn_count` fits) — the shape half
    # is dropped, never coerced and never raised.
    stmts = _narrative_statements([unrepresentable])
    assert len(stmts) == 1
    # ⚠️ Written as the RESET the projection documents ("carried but
    # unrepresentable ⇒ no count stated"), never as an absent column and never
    # as a coerced 7.
    assert "capture_redactions = NULL" in _set_clause(stmts[0])
    assert 7 not in stmts[0].params
    # A record whose WHOLE payload is unrepresentable contributes nothing at
    # all: an all-NULL insert would read as "session known, no text", a claim
    # no record made.
    assert _narrative_statements([{"type": "SessionRecorded", "id": SESSION,
                                   "capture_redactions": {"total": 7}}]) == []


# ── §3 the read path: record-only, and a miss is PROVEN ─────────────────────

def test_the_read_path_touches_no_journal_and_no_graph():
    """§9.4's fetch must survive the graph, so it may not read it.

    An import of `tortoise.log` or the cache reader here would be a slow,
    invisible coupling: it would work in every test and fail exactly when the
    graph is gone — the one situation the path exists for.
    """
    for module in (record_text, record_read):
        tree = ast.parse(Path(module.__file__).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {m for m in imported
                     if m in {"tortoise.log", "tortoise.cache_projection",
                              "tortoise.projection", "tortoise.rebuild"}
                     or m.startswith("tortoise.projection.")}
        assert not forbidden, (
            f"{module.__name__} imports {sorted(forbidden)} — §9.4's fetch would "
            "then depend on the store it is supposed to outlive")


def test_the_verb_is_WIRED_to_the_composition():
    """An import-only pin is satisfied by a body that returns a constant.

    The `record-reconcile` lesson: the verb must CALL `serve_from_record`, and
    the dispatcher must route `record-text` to it. Both are pinned on the AST,
    not on a substring of the source.
    """
    tree = ast.parse((REPO_ROOT / "tortoise" / "__main__.py").read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_cmd_record_text"),
              None)
    assert fn is not None, "`record-text`'s command function is gone"
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "serve_from_record" in called, (
        "`_cmd_record_text` does not call `serve_from_record` — the verb would "
        "be an import with a story")
    assert "scoped" in called, (
        "the verb lost the record's tenant prologue — a tenancy-filtered read "
        "would then be reported as an honest absence (the G1 defect)")
    routes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Compare)
              and isinstance(n.left, ast.Attribute) and n.left.attr == "cmd"
              and any(isinstance(c, ast.Constant) and c.value == "record-text"
                      for c in n.comparators)]
    assert routes, "`record-text` is not dispatched in main()"


def test_a_read_time_database_error_is_EXIT_UNAVAILABLE_not_a_traceback(
        monkeypatch, capsys):
    """`2` is "no verdict" — including when the READ itself blows up.

    PR #9862 review (P2): the verb wrapped `record_dsn`, `psycopg2.connect` and
    `scoped(...)`, then caught only `RecordReadError` / `ValueError` around the
    read. `record_read` executes plain cursor SQL and raises the DRIVER's own
    exceptions, and `main()` has no dispatch-level handler — so a read-time
    failure escaped as an uncaught traceback and exit `1`, a code the contract
    does not define. A scheduler keyed on `0/2/3` could not classify it, and the
    operator lost the diagnostic the verb exists to print. The sibling verb
    (`record-reconcile`) already maps its read failure to `EXIT_UNAVAILABLE`.

    ⛔ NON-VACUOUS ON THE PRE-FIX CODE: the arm this exercises did not exist, so
    the fake's exception propagated out of `cli.main(...)` and this test errored
    instead of asserting. Three causes are reachable in production and all three
    take this path: `UndefinedTable` (this migration not applied — the likely
    FIRST run), `QueryCanceled` (the verb installs a 5 s `statement_timeout`),
    `OperationalError` (connection dropped between the prologue and the read).

    DB-FREE on purpose: only `psycopg2` and the connection are faked, so the
    arm is measured wherever the suite runs rather than only in the armed
    Postgres job.
    """
    import sys as _sys
    import types as _types

    from tortoise import __main__ as cli

    org_id, graph_id = "org-9555-read-boom", "g-9555-read-boom"

    class _UndefinedTable(Exception):
        """The driver's own class of failure — a name, not a contract."""

    class _Cursor:
        def __init__(self):
            self.rows: list = []
            self.row = None

        def execute(self, sql, params=None):
            self.rows, self.row = [], None
            if sql == rp.TENANT_GUC_SQL:
                return                      # the transaction-local set
            if sql == rp.TENANT_SCOPE_SQL:  # the read-back: scoped, non-bypassing
                self.row = (org_id, False)
                return
            if rp.NARRATIVE_TABLE in sql:
                raise _UndefinedTable(
                    f'relation "public.{rp.NARRATIVE_TABLE}" does not exist')
            # every other read is an honest miss

        def fetchone(self):
            return self.row

        def fetchall(self):
            return list(self.rows)

    class _Conn:
        """The two assignments the verb makes, then the reads above."""

        def __init__(self):
            self.autocommit = True
            self.closed = False

        def cursor(self):
            return _Cursor()

        def close(self):
            self.closed = True

    conn = _Conn()
    monkeypatch.setitem(_sys.modules, "psycopg2", _types.SimpleNamespace(
        connect=lambda dsn, **kw: conn))

    rc = cli.main(["record-text", "--record-dsn", "postgresql://fake/record",
                   "--org", org_id, "--graph", graph_id,
                   "--session", SESSION])
    err = capsys.readouterr().err

    assert rc == record_text.EXIT_UNAVAILABLE, (
        f"a read-time DB error exited {rc}, not {record_text.EXIT_UNAVAILABLE} "
        f"— `2` is 'no verdict' and an uncaught traceback is not in the "
        f"contract at all (stderr: {err})")
    assert rc != record_text.EXIT_ABSENT, (
        "an unreadable record was reported as an ABSENCE (exit 3): nothing was "
        "proven about what the record holds")
    # The diagnostic: what failed, and which exit code the operator got.
    assert "unreadable" in err, err
    assert "_UndefinedTable" in err, (
        f"the failure's type is not named, so the operator cannot tell a "
        f"missing migration from a cancelled query (stderr: {err})")
    assert conn.closed, "the connection was not closed on the failure path"


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """The smallest record connection: answers per statement substring."""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []

    def cursor(self):
        conn = self

        class _C(_Cursor):
            def execute(self, sql, params=None):
                conn.seen.append((sql, params))
                for needle, rows in conn.answers:
                    if needle in sql:
                        self._rows = rows
                        return
                self._rows = []
        return _C([])


def test_the_scope_proof_is_the_records_OWN_prologue():
    """The G1 refusal, fixed the way the review demanded (PR #9862, P1).

    ⛔ A first draft proved the scope by reading `public.graphs`. Measured in
    review: that table is granted to NO record role, so as
    `tortoise_record_app` (the role the runbook mandates) the SELECT raises
    `InsufficientPrivilege` and the verb answered exit 2 on every call; and
    where it WAS readable it proved the wrong predicate, because "can I see
    `graphs`" says nothing about whether the text tables' policy filtered the
    text — so a filtered read still came back as an honest absence (exit 3).

    The proof must be the record's own tenant prologue
    (`record_projection.scope_connection`): it sets `app.current_org_id`
    transaction-locally, READS IT BACK, and refuses a connection whose role can
    bypass RLS. This pin holds the verb to that, and to the property that makes
    a zero-row answer meaningful: the GUC, not a table, decides.
    """
    source = Path(record_text.__file__).read_text()

    # ⛔ ON THE AST, NOT ON A SUBSTRING: the module's docstrings NAME
    # `record_projection.scope_connection` while explaining the design, so a
    # substring pin is satisfied by gutting `scoped()` and leaving the prose —
    # a false PASS at the unit level, caught only by an armed live leg.
    tree = ast.parse(source)
    scoped_fn = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "scoped"),
                     None)
    assert scoped_fn is not None, "record_text.scoped() is gone"
    # A CALL, not a name in the body: `scoped`'s own docstring names
    # `scope_connection`, so dumping the body (or searching the source) is
    # satisfied by prose. Only a call site proves the prologue runs.
    called = {n.func.id for n in ast.walk(scoped_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "scope_connection" in called, (
        "record_text.scoped() no longer CALLS the record's own tenant prologue "
        "(the name surviving only in prose is the false pass this pin exists "
        "to prevent)")

    # The pin is on the SQL these two modules EXECUTE, not on their prose: their
    # docstrings explain the abandoned draft (and name `public.graphs` while
    # doing so), so a substring search over the file reds on its own
    # explanation. Walk every `.execute(<string literal>, …)` call instead.
    executed: list[str] = []
    for module in (record_text, record_read):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                executed.append(node.args[0].value)
    assert executed, (
        "no SQL found in either module — this pin stopped looking at anything")
    assert not [q for q in executed if "public.graphs" in q], (
        "these modules EXECUTE a read of `public.graphs`: no record role is "
        "granted that table, so the verb would exit 2 on every sanctioned DSN")

    # And a library-level miss is still an honest `None` (the VERB is what turns
    # an unserialisable scope into EXIT_UNAVAILABLE, not the reader).
    empty = _Conn([("session_narrative", []), ("public.point", [])])
    assert record_text.serve_from_record(
        empty, org_id="o", graph_id="g", session_id=SESSION).session is None
    assert record_text.EXIT_ABSENT != record_text.EXIT_UNAVAILABLE


def test_an_unrepresentable_limit_never_claims_the_transcript_is_empty():
    """`--limit 0` renders no turn while the record holds several.

    The bare `or ["(none recorded)"]` fallback then printed "(none recorded)"
    one line under "turns in record: 2" — a false statement about the RECORD,
    made by the very block that exists to serve its evidence (PR #9862 review,
    P2).
    """
    sess = record_read.SessionText(
        session_id=SESSION, narrative=NARRATIVE, redactions=4, turn_count=2,
        turns=(record_read.Turn(turn_id=f"{SESSION}_t0", index=0,
                                content="[user] hi", speaker="user"),
               record_read.Turn(turn_id=f"{SESSION}_t1", index=1,
                                content="[assistant] there", speaker="assistant")))
    rendered = record_text.TextOutcome(key=SESSION, resolved_by=record_text.BY_SESSION,
                                       session=sess, shown=0, limit=0).render()
    assert "(none recorded)" not in rendered.split("── transcript")[1], (
        "`--limit 0` claims the record holds no turn: the transcript block is "
        "false about the record it is serving")
    assert "turns in record: 2" in rendered
    assert "2 more turn(s) not shown" in rendered

    # A record that genuinely holds NO turn still says so — the fallback is
    # correct where it is true.
    bare = record_read.SessionText(session_id=SESSION, narrative=NARRATIVE)
    assert "(none recorded)" in record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION, session=bare).render()


def test_the_absent_line_names_the_key_the_door_actually_used():
    """A `--source-url` miss is not a session id, and must not be called one.

    PR #9862 review (P3): both doors printed
    ``no §9.1 text in the record for <door> '<key>' (session '<key>')`` — the
    same value twice, and the parenthetical asserted a KIND the `--source-url`
    door does not have. An operator reading that string is told the key is a
    session and goes looking for a session that does not exist.
    """
    absent_by_source = record_text.TextOutcome(
        key=f"session:{SESSION}", resolved_by=record_text.BY_SOURCE)
    by_source = absent_by_source.render()
    assert "source-url" in by_source, by_source
    assert "(session" not in by_source, (
        f"the `--source-url` miss labels its key a SESSION — the key is the "
        f"source url, and nothing proved it is a session id: {by_source}")
    assert by_source.count(f"session:{SESSION}") == 1, (
        f"the key is printed twice, so one of the two labels is a guess: "
        f"{by_source}")

    absent_by_session = record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION)
    by_session = absent_by_session.render()
    assert "session" in by_session, by_session
    assert by_session.count(SESSION) == 1, by_session
    # Both arms still say what an absence IS — the record was readable and held
    # nothing — because that is the whole 3-versus-2 distinction.
    assert "no §9.1 text" in by_session, by_session
    assert "no §9.1 text" in by_source, by_source


def test_a_source_url_miss_reports_WHICH_miss_it_was():
    """An unresolved LINK and an empty SESSION are not the same absence.

    PR #9862 review (P2): `record_read.source_narrative` returns ``None`` in
    three situations — no source row for the url, a row whose `session_id` is
    NULL, and a link that resolves to a session holding no text — and the verb
    printed *"the source's session was resolved, and the record holds no text
    for it"* for ALL of them. For the first two the resolution step never
    happened, so the one string an operator debugging "why is there no text?"
    reads first named a proof that was not performed (and `--json` reported
    `session_id: null` for every case, so it could not tell them apart either).

    Pre-fix this fails on the WORDING (the message said "the source's session was
    resolved" for an unresolved link), not merely on a missing field: the defect
    is the sentence an operator reads.
    """
    # (1) NO SOURCE ROW: the link cannot resolve, and nothing may claim it did.
    no_link = record_text.serve_from_record(
        _Conn([]), org_id="o", graph_id="g", source_url=f"session:{SESSION}")
    # ⛔ THE WORDING IS CHECKED FIRST, so this leg fails on the PRE-FIX message
    # ("the source's session was resolved, and the record holds no text for it")
    # rather than merely on a field that did not exist yet: the defect is the
    # sentence an operator reads, not the absent attribute.
    rendered = no_link.render()
    assert "no §9.1 text" in rendered, rendered
    assert "nothing was resolved" in rendered, (
        f"a url with no `:Source` row reports a resolution that never "
        f"happened: {rendered}")
    assert "session was resolved" not in rendered, (
        f"the miss claims the resolution step ran when no link was found: "
        f"{rendered}")
    assert "no source row" in rendered, rendered
    assert not no_link.served
    assert no_link.source_linked is False, (
        "an unresolved link reported itself as resolved (source_linked is not "
        f"False): {no_link.source_linked!r}")
    assert no_link.linked_session_id is None
    assert no_link.as_dict()["source_linked"] is False, (
        "`--json` cannot distinguish the two misses, so it re-reports the "
        "absence without its cause")

    # (2) THE ROW EXISTS BUT ITS session_id IS EMPTY: still an unresolved link.
    blank = record_text.serve_from_record(
        _Conn([("public.source", [("",)])]),
        org_id="o", graph_id="g", source_url=f"session:{SESSION}")
    blank_render = blank.render()
    assert "nothing was resolved" in blank_render, (
        "a source row with no session link is reported as a resolution, so "
        f"the message again narrates a proof that never happened: {blank_render}")
    assert blank.source_linked is False, (
        "a source row with a blank session_id counts as resolved")

    # (3) THE LINK RESOLVES, the session holds nothing: now the resolution IS
    #     proven, and the miss may say so — naming the session it resolved to.
    linked = record_text.serve_from_record(
        _Conn([("public.source", [(SESSION,)]), ("session_narrative", []),
               ("public.point", [])]),
        org_id="o", graph_id="g", source_url=f"session:{SESSION}")
    linked_render = linked.render()
    assert f"resolved to session {SESSION!r}" in linked_render, (
        f"a resolved link does not name the session it resolved to, so the "
        f"operator cannot go and look at it: {linked_render}")
    assert "no source row" not in linked_render, linked_render
    assert not linked.served
    assert linked.source_linked is True, linked.source_linked
    assert linked.linked_session_id == SESSION
    assert record_text.exit_code_for(linked) == record_text.EXIT_ABSENT
    assert linked.as_dict()["linked_session_id"] == SESSION
    # The two misses are distinguishable from `--json` ALONE — the second
    # signal the review measured as missing.
    assert linked.as_dict()["source_linked"] != no_link.as_dict()["source_linked"]

    # An outcome that never probed the link (the default) must not assert a
    # resolution either: the fail-closed read of an unmeasured field.
    unprobed = record_text.TextOutcome(
        key=f"session:{SESSION}", resolved_by=record_text.BY_SOURCE).render()
    assert "session was resolved" not in unprobed, unprobed
    assert "nothing was resolved" in unprobed, unprobed


def test_the_exit_code_for_a_served_and_an_absent_answer():
    served = record_text.TextOutcome(
        key=SESSION, resolved_by=record_text.BY_SESSION,
        session=record_read.SessionText(session_id=SESSION, narrative=NARRATIVE))
    absent = record_text.TextOutcome(key=SESSION, resolved_by=record_text.BY_SESSION)
    assert record_text.exit_code_for(served) == record_text.EXIT_SERVED
    assert record_text.exit_code_for(absent) == record_text.EXIT_ABSENT
    assert served.as_dict()["narrative"] == NARRATIVE
    assert served.as_dict()["served"] is True
    assert absent.as_dict()["served"] is False


def test_both_keys_or_neither_is_refused():
    conn = _Conn([])
    for kwargs in ({}, {"session_id": SESSION, "source_url": "u"}):
        with pytest.raises(ValueError):
            record_text.serve_from_record(conn, org_id="o", graph_id="g", **kwargs)


# ── §4 LIVE: the record-sourced rebuild reproduces the narrative ────────────

def _record_db():
    """A connection to a Postgres carrying the record migrations, or skip.

    The SAME arming surface as the record's other Postgres legs
    (`tests/test_9078_record_written_by_production_path.py`): the
    `test-record-postgres` CI job provisions a throwaway Postgres and exports
    `TEST_RECORD_DB_URI`, and its leg gate fails the job when a leg SKIPS
    (#9551) — an unarmed job and an armed-but-skipping one are otherwise
    indistinguishable from outside.
    """
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the R7 proof")
    psycopg2 = pytest.importorskip("psycopg2",
                                   reason="psycopg2 is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _ensure_schema(conn) -> None:
    from test_9078_record_written_by_production_path import _ensure_record_schema
    _ensure_record_schema(conn)


def _replay_db():
    """The connection the REPLAY runs on — the app role, inside a transaction.

    Two properties, both enforced rather than conventional (`#9600` review
    cycles 1/2, R6 #9554), and both of which `_record_db()` deliberately lacks:

    * the APPLICATION role (`tortoise_record_app`, NOBYPASSRLS) — `replay()`
      REFUSES a connection whose `current_user` can bypass RLS, and the
      provisioning superuser is every bypass at once;
    * NOT autocommit — the tenant GUC is transaction-local and read back before
      the plan, so on an autocommit connection the set is gone by the read-back
      and the replay is refused. `replay()` states the CALLER owns the
      transaction; a non-autocommit connection is its supported shape.

    Reused from the record's other Postgres legs rather than re-derived: the
    `options=-c role=…` construction has its own measured gotcha (`quote_via`,
    because libpq does not decode `+` as a space), and a second copy would
    eventually miss it.
    """
    from test_9078_record_written_by_production_path import _record_replay_db
    return _record_replay_db()


def _tenant() -> tuple[str, str]:
    """A FRESH `(org_id, graph_id)` pair for this run.

    Both halves are unique, and that is not tidiness: `graphs.id` is the
    PRIMARY KEY, so a constant `graph_id` collides across runs with a different
    `org_id` and an `ON CONFLICT DO NOTHING` then leaves the new scope with NO
    graph row — which the narrative's FK reports as *"Key is not present in
    table graphs"*, i.e. a confusing failure in a fixture rather than in the
    thing under test.
    """
    tag = uuid.uuid4().hex[:8]
    return f"org-{tag}", f"g-{tag}"


def _seed(conn, *, org_id: str, graph_id: str, with_source: bool = False) -> None:
    """Journal-replay the capture's records into a FRESH tenant scope.

    A NEW `org_id`/`graph_id` pair per run, so a re-run (or a sibling lane on
    the same container) cannot inherit a previous narrative and pass on it —
    the isolation is the tenant, which is what the record's own key shape gives.

    ``with_source`` adds the capture's own transcript `:Source` record, which is
    the record that carries `story_arc` onto a NODE (`hosted_api`
    `_execute_commit_writes` step 2 does exactly that MERGE). The cache rebuild
    needs it: without a `:Source` record there is nothing in the record for the
    rebuild to reproduce the narrative ON, so "the cache holds the narrative" is
    unmeasurable and a rebuild that writes only the turn Points satisfies every
    count.
    """
    # The record's tenancy parents: `organizations(id)` and `graphs(org_id, id)`.
    # `session_narrative`'s FK is `(org_id, graph_id) -> graphs(org_id, id)`, so
    # the scope row must exist before a narrative can land — the contract
    # #9638's migration chose (the graph is the anchor, deliberately NOT the
    # session row).
    #
    # The tenant rows go in on the SEEDING connection (the provisioning
    # superuser must see every tenant); the REPLAY goes in on the app role, in
    # one transaction — see `_replay_db`.
    conn.cursor().execute(
        "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING", (org_id, org_id))
    conn.cursor().execute(
        "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING", (graph_id, org_id, graph_id))
    replay_conn = _replay_db()
    try:
        records = [
            # The turn rows §9.4's verbatim text comes from (`{session}_t{i}`),
            # as the capture's own `_write_capture_turns` journals them.
            {"type": "PointAdded", "id": f"{SESSION}_t0",
             "ts": "2026-10-24T00:00:00Z",
             "point": {"id": f"{SESSION}_t0",
                       "content": "[user] what does a source hold?",
                       "pointKind": "event", "speaker": "user",
                       "is_episodic": True, "status": "draft",
                       "createdAt": "2026-10-24T00:00:00Z", "embedding": None}},
            {"type": "PointAdded", "id": f"{SESSION}_t1",
             "ts": "2026-10-24T00:00:01Z",
             "point": {"id": f"{SESSION}_t1",
                       "content": "[assistant] the narrative, plus its raw turns.",
                       "pointKind": "event", "speaker": "assistant",
                       "is_episodic": True, "status": "draft",
                       "createdAt": "2026-10-24T00:00:01Z", "embedding": None}},
            _narrative_record(),
            _counts_record(redactions=4, turns=2),
        ]
        if with_source:
            # The transcript `:Source` the commit's raw MERGE writes
            # `s.story_arc=$arc` onto — the SAME text the carrier states, so the
            # record and the cache state one fact (and the rebuild has to
            # reproduce it on a node, not merely re-count rows).
            records.append({
                "type": "SourceCreated",
                "id": f"session:{SESSION}",
                "ts": "2026-10-24T00:00:02Z",
                "url": f"session:{SESSION}",
                "sessionId": SESSION,
                "story_arc": NARRATIVE,
                "summary": SUMMARY,
            })
        rp.replay(records, replay_conn, org_id=org_id, graph_id=graph_id)
        replay_conn.commit()
    except Exception:
        replay_conn.rollback()
        raise
    finally:
        replay_conn.close()


def test_the_public_capture_path_lands_the_narrative_in_the_record():
    """(b), first half: a journal replay puts §9.1's text in the record.

    Measured pre-fix: the same run left `narrative` NULL, because no record
    carried `story_arc` at all. The redaction count is the second indicator —
    R4's `capture_redactions`, reproduced from the record, not asserted from the
    journal.
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, session_id=SESSION)
        assert outcome.served, "the record holds no §9.1 text after a replay"
        assert outcome.session.narrative == NARRATIVE
        assert outcome.session.redactions == 4
        assert outcome.session.turn_count == 2
        assert outcome.session.summary == SUMMARY
        # §9.1's "raw transcript" comes back from the record's OWN turn rows —
        # never a second copy of the text.
        assert [t.body for t in outcome.session.turns] == [
            "what does a source hold?", "the narrative, plus its raw turns."]
        assert outcome.session.transcript.startswith("user: what does a source hold?")
    finally:
        conn.close()


def test_the_public_capture_path_reports_an_UNLINKED_source_url_honestly():
    """A `--source-url` the tenant has no `:Source` for: absence, not a guess.

    PR #9862 review (P2) measured that this branch was never exercised end to
    end: the only live `--source-url` leg seeds the row first, so nothing proved
    what an operator is told when the URL is wrong/uncaptured — and the message
    asserted *"the source's session was resolved"* regardless. This is the leg
    that seeds NO source row, so the wording has to match what actually
    happened: the LINK did not resolve (and the record was still provably read,
    which is what makes `3` an answer rather than a guess).
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
        url = f"session:{SESSION}-never-captured"

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, source_url=url)
        assert not outcome.served, (
            "a source url with no :Source row served text — the fixture is "
            "wrong or the reader resolves the wrong key")
        # The SENTENCE first: the defect is what the operator reads, so this leg
        # fails on the pre-fix wording rather than only on a new field.
        rendered = outcome.render()
        assert "nothing was resolved" in rendered, (
            f"no :Source row exists for {url!r}, so nothing could resolve, yet "
            f"the verb's miss narrates a resolution: {rendered}")
        assert "session was resolved" not in rendered, rendered
        assert outcome.source_linked is False, (
            f"no :Source row exists for {url!r}, so nothing could resolve, "
            f"yet the outcome reports link={outcome.source_linked!r}")
        assert outcome.linked_session_id is None
        # `3`, i.e. an honest absence: the tenant WAS scoped and read (the
        # verb installs the prologue before any read), so nothing here is a
        # no-verdict.
        assert record_text.exit_code_for(outcome) == record_text.EXIT_ABSENT
    finally:
        conn.close()


def test_the_public_capture_path_serves_S9_4_text_by_SOURCE_URL():
    """(c): §9.4's shape — "show me the verbatim text behind this claim".

    The link is `public.source.session_id`, a COLUMN, so it resolves with the
    graph destroyed. This is the arm the ask path needs and did not have.
    """
    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
        url = f"session:{SESSION}"
        # The `:Source` row with the link §9.4 resolves through. Written here
        # rather than by the projection on purpose: this leg is about the READ
        # arm, and the WRITE arm for a source row already exists
        # (`SourceCreated`), so re-proving it would blur what failed.
        # `public.source` is a NODE table: its row is anchored by
        # `node_identity (org_id, graph_id, node_label, node_key)`, so the
        # identity row comes first.
        conn.cursor().execute(
            "INSERT INTO public.node_identity (org_id, graph_id, node_label, "
            "node_key) VALUES (%s, %s, 'Source', %s) "
            "ON CONFLICT DO NOTHING", (org_id, graph_id, url))
        conn.cursor().execute(
            "INSERT INTO public.source (org_id, graph_id, node_label, node_key, "
            "id, url, session_id) VALUES (%s, %s, 'Source', %s, %s, %s, %s) "
            "ON CONFLICT (org_id, graph_id, node_key) DO UPDATE "
            "SET session_id = EXCLUDED.session_id",
            (org_id, graph_id, url, url, url, SESSION))

        outcome = record_text.serve_from_record(
            conn, org_id=org_id, graph_id=graph_id, source_url=url)
        assert outcome.served
        assert outcome.resolved_by == record_text.BY_SOURCE
        assert outcome.session.narrative == NARRATIVE
        assert outcome.session.redactions == 4
    finally:
        conn.close()


def test_the_public_capture_path_serves_S9_4_text_through_the_VERB(tmp_path):
    """(c), end to end: `python -m tortoise record-text` IS the fetch path.

    The library legs above prove the reader; this one proves an OPERATOR can
    reach it, which is the whole difference between a reader and a path (the
    measured #9555 defect was a reader with no caller).

    ⛔ THE VERB RUNS AS THE APP ROLE, not as the provisioning superuser, and that
    is not incidental — it is the P1 the review of this PR measured. `record-text`
    installs the record's tenant prologue, which (a) refuses a role that can
    bypass RLS, so the superuser DSN exits `2`, and (b) is what makes a zero-row
    answer meaningful. A leg that ran as the superuser would pass while the
    sanctioned configuration was inert. Exit codes are asserted explicitly,
    because they are the operator's next step:

    * `0` and the text for a session the record holds,
    * `3` for a session (or a whole org) it does not — the tenant was SCOPED and
      READ, so an honest absence,
    * `2` for a connection that cannot be scoped at all — no verdict, never an
      absence.

    The `--json` shape is asserted on the keys R7's indicators are measured
    from (`narrative`, `redactions`, `turns`), so a rendering change cannot
    quietly drop one of them.
    """
    import subprocess
    import sys

    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the R7 verb proof")
    pytest.importorskip("psycopg2", reason="psycopg2 is the record's driver")

    from test_9078_record_written_by_production_path import _app_role_dsn

    conn = _record_db()
    try:
        _ensure_schema(conn)
        org_id, graph_id = _tenant()
        _seed(conn, org_id=org_id, graph_id=graph_id)
    finally:
        conn.close()

    app_dsn = _app_role_dsn(dsn)

    def _run(*extra, dsn_override=None):
        return subprocess.run(
            [sys.executable, "-m", "tortoise", "record-text",
             "--record-dsn", dsn_override or app_dsn,
             "--org", org_id, "--graph", graph_id, *extra],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)

    served = _run("--session", SESSION, "--json")
    assert served.returncode == 0, served.stderr
    payload = json.loads(served.stdout)
    assert payload["served"] is True
    assert payload["narrative"] == NARRATIVE
    assert payload["redactions"] == 4
    assert payload["turn_count"] == 2
    assert payload["turns"] == 2
    assert payload["verbatim_available"] is True

    # A human-run form: the narrative and the verbatim turns, in one screenful.
    rendered = _run("--session", SESSION)
    assert rendered.returncode == 0, rendered.stderr
    assert NARRATIVE.splitlines()[0] in rendered.stdout
    assert "user: what does a source hold?" in rendered.stdout

    # `--limit` bounds the RENDERING only: the reported count is the record's.
    bounded = _run("--session", SESSION, "--limit", "1", "--json")
    assert bounded.returncode == 0, bounded.stderr
    assert json.loads(bounded.stdout)["turns"] == 2
    assert json.loads(bounded.stdout)["shown"] == 1

    absent = _run("--session", "no-such-session", "--json")
    assert absent.returncode == 3, absent.stderr
    assert json.loads(absent.stdout)["served"] is False

    # ⛔ THE P1 THIS LEG EXISTS FOR: the provisioning superuser BYPASSES RLS, so
    # the tenant prologue refuses it. A `2` here is the verb declining to answer
    # from a connection whose "0 rows" would not mean anything about one tenant.
    bypassing = _run("--session", SESSION, dsn_override=dsn)
    assert bypassing.returncode == record_text.EXIT_UNAVAILABLE, bypassing.stderr
    assert "could not be scoped" in bypassing.stderr

    # Both keys (or neither) is refused before anything is read.
    both = _run("--session", SESSION, "--source-url", "u")
    assert both.returncode == record_text.EXIT_UNAVAILABLE
    assert "exactly one" in both.stderr


# ── §5 the record type's CLASSIFICATIONS (each one is a refusal if wrong) ────

def test_a_torn_narrative_line_cannot_block_recovery():
    """`log.TORN_TAIL_HARMLESS_EVENT_TYPES` — the polarity is REFUSE by default.

    A new journal type that is NOT listed there makes `tortoise rebuild`,
    `recover_from_log` and `backup.restore` REFUSE a journal whose final line was
    cut off mid-append (the S15 case the tolerance exists for). For a type whose
    loss is a graph no-op that trade is backwards, and PR #9862's review measured
    exactly that: a SIGKILL-truncated `SessionNarrated` tail refused the replay
    while `EntityBindingRefused` (folded nowhere, like this one) was allowed.

    The assertion is derived from the FOLD classification rather than re-typed:
    any record type in `_NO_PROJECTION_FOLD` folds nowhere, so dropping a torn
    one cannot revive graph state.
    """
    from tortoise import log as tlog
    from tortoise.projection import _NO_PROJECTION_FOLD
    assert rp.NARRATIVE_RECORD_TYPE in _NO_PROJECTION_FOLD
    assert rp.NARRATIVE_RECORD_TYPE in tlog.TORN_TAIL_HARMLESS_EVENT_TYPES, (
        "a torn `SessionNarrated` line would block `rebuild`/`recover_from_log`/"
        "`backup.restore` — its loss is a graph no-op (it folds nowhere), so the "
        "refusal buys nothing and costs the whole journal's recovery")


def test_the_record_type_cannot_bypass_the_replay_registry():
    """`mutations_for` is TOTAL: an undeclared type must still raise, and this
    one must be declared on BOTH sides of the vocabulary assert."""
    with pytest.raises(rp.UnknownJournalRecordType):
        rp.mutations_for({"type": "SessionNarratedTypo", "id": "x"},
                         org_id="o", graph_id="g")
    assert rp.NARRATIVE_RECORD_TYPE in rp.RECORD_MUTATIONS
    assert rp.NARRATIVE_RECORD_TYPE in rp.JOURNAL_EVENT_TYPES
