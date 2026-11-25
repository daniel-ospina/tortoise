"""#13043 review P1-1 / P1-2 / P2-2 / P2-4 — the operator door's preconditions.

WHY THIS FILE EXISTS. `record_allocator_cli.py` is the ONLY way to reach the
allocator, and the review found that it let an operator `seed` + `arm` while
`TORTOISE_RECORD_ALLOCATE` was unset — a state that EATS ROWS (the door is on the
enumerated arm while the graph is cut over, so `_refuse_an_enumerated_replay`
refuses every record while the journal keeps advancing; `write_live_record` is
fail-soft by contract, so this is a WARN per record and never an exception). It
also found that the flag is GLOBAL while the cutover is PER GRAPH, and that
nothing in this tree creates the counter row for a NEW graph
(`provision_graph_record` has zero hits), so every graph created after the
cutover is born uncut.

The fixes asserted here:

* `arm` REFUSES without `--acknowledge-cutover`, and the refusal is the
  documentation: it names the env flag, its HOME (the writer, not this shell),
  the order of operations, the per-graph scope, the absent creation hook, and
  the backfill lane.
* `sweep` finds born-uncut graphs the record has seen (read-only), so the hazard
  is discoverable rather than a sentence.
* `status` reports the RECORD's state and the flag SEPARATELY (P2-4), and the
  DSN name is IMPORTED from the seam rather than copied (P2-2).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_spec = importlib.util.spec_from_file_location(
    "record_allocator_cli", _TOOLS / "record_allocator_cli.py")
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


# ── P1-2: `arm` needs the acknowledgement, and the refusal IS the doc ──────

def test_arm_refuses_without_the_acknowledgement(capsys) -> None:
    rc = cli.main(["arm", "--org-id", "o", "--graph-id", "g"])
    assert rc == cli.EXIT_REFUSED, (
        "`arm` proceeded without an acknowledgement — seed+arm followed by a "
        "flag nobody rolled is the state that eats rows")
    captured = capsys.readouterr()
    err, out = captured.err, captured.out
    for required in ("TORTOISE_RECORD_ALLOCATE",   # the flag, by name
                     "WRITER",                     # …and whose environment it lives in
                     "halt", "seed", "reopen",     # the way back, in order
                     "provision_graph_record",     # the hook that is absent
                     "sweep",                      # how the hazard is found
                     "backfill_copy_turns"):       # the fourth position-row writer
        assert required in err, (
            f"the refusal does not mention {required!r}, so an operator learns "
            f"it only from the incident:\n{err}")
    # ⛔ THE ACK IS NOT A KEYSTROKE THE TOOL PERFORMS: nothing was armed.
    assert "arm" in out


def test_the_acknowledgement_is_only_a_gate_not_a_DSN_bypass(monkeypatch) -> None:
    """With the ack, `arm` gets PAST the gate and then fails on what it lacks.

    Non-vacuity for the refusal above: a `main` that refused EVERYTHING would
    pass that test. Unset the DSN and the acknowledged form must reach the DSN
    check (EXIT_USAGE) instead of the gate (EXIT_REFUSED).
    """
    monkeypatch.delenv(cli._record_env_contract()[0], raising=False)
    rc = cli.main(["arm", "--org-id", "o", "--graph-id", "g",
                   "--acknowledge-cutover"])
    assert rc == cli.EXIT_USAGE, (
        "an acknowledged `arm` with no DSN should fail on the DSN, not on the gate")


def test_every_graph_scoped_verb_still_requires_a_graph_id(monkeypatch) -> None:
    """`sweep` is org-scoped; nothing else may run with `--graph-id` omitted."""
    monkeypatch.delenv(cli._record_env_contract()[0], raising=False)
    for verb in ("status", "seed", "arm", "halt", "reopen"):
        with pytest.raises(SystemExit) as excinfo:
            cli.main([verb, "--org-id", "o", "--enumerated-floor", "0"])
        assert excinfo.value.code == cli.EXIT_USAGE
    # …and `sweep` does not require one. ⛔ THE DSN IS UNSET HERE (#13043 review
    # V3): without the `delenv` above, the assertion was decided by the AMBIENT
    # environment — on a box with `TORTOISE_RECORD_DSN` exported `sweep` reaches
    # `_connect` for real and can answer EXIT_OK, so the row tested the shell
    # rather than the code.
    assert cli.main(["sweep", "--org-id", "o"]) == cli.EXIT_USAGE


# ── P2-2 / P2-4: one name for the DSN, two readings for "allocates" ────────

def test_the_DSN_name_is_IMPORTED_from_the_seam_not_copied() -> None:
    """A second spelling of one name is how an operator seeds one record and
    arms another — the comment already claimed "one name", the code copied it."""
    from tortoise import record_writer as rw

    dsn_env, allocate_env = cli._record_env_contract()
    assert dsn_env == rw.RECORD_DSN_ENV
    assert allocate_env == rw.RECORD_ALLOCATE_ENV
    source = (_TOOLS / "record_allocator_cli.py").read_text()
    assert 'RECORD_DSN_ENV = "TORTOISE' not in source, (
        "the CLI re-declares the DSN name instead of importing the seam's")


def test_the_sweep_query_is_org_scoped_and_reads_the_ROW_TABLE_not_graphs() -> None:
    """`tortoise_record_app` has NO grant on `public.graphs`, so a sweep over it
    would fail on exactly the role the CLI must connect as."""
    from tortoise import record_allocator as ra

    sql = ra.UNCUT_GRAPHS_SQL
    assert "public.journal_event" in sql
    assert "public.graphs" not in sql, (
        "UNCUT_GRAPHS_SQL reads public.graphs, which the record role cannot see")
    assert "public.record_cursor" in sql and "IS NULL" in sql
    assert sql.count("%s") == 1, "the sweep must be scoped to ONE org"
    # ⛔ AND THE POPULATION IS EVERYTHING NOT ALLOCATING (#14624 review 1):
    # `WHERE c.graph_id IS NULL` alone read CLEAN over the `seeded` graphs
    # `backfill` leaves behind, because a `seeded` row allocates nothing either.
    assert "c.mode" in sql, (
        "the sweep no longer selects the counter's mode — membership cannot tell "
        "`seeded` (heal with `arm`) from `halted` (heal with `reopen`)")
    assert "<> 'active'" in sql, (
        f"the population must exclude only the TERMINAL mode: {sql}")


def test_status_reports_the_record_state_and_the_box_separately() -> None:
    """P2-4: `allocates` printed `true` on a graph whose live door is unarmed."""
    from tortoise import record_allocator as ra

    class _Cur:
        def __init__(self): self._rows = [(0,)]
        def execute(self, *a): pass
        def fetchone(self): return self._rows[0]
        def fetchall(self): return self._rows
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        closed = False
        def cursor(self): return _Cur()
        def rollback(self): pass

    from tortoise import record_allocator as ra_mod
    original = ra_mod.read_cursor
    ra_mod.read_cursor = lambda *a, **k: (7, ra_mod.MODE_ACTIVE)
    try:
        payload = cli._cmd_status(
            _Conn(), type("A", (), {"org_id": "o", "graph_id": "g",
                                    "floor": None})())
    finally:
        ra_mod.read_cursor = original
    assert payload["allocates"] is True
    assert payload["allocates_scope"] == "record", (
        "`allocates` is not labelled: a reader takes it for the DOOR's state")
    assert payload["record_allocate_env"] == "TORTOISE_RECORD_ALLOCATE"
    assert "record_allocate_seen_here" in payload
    assert ra is ra_mod


# ── the sweep, against a real record: it must FIND the graph ───────────────

_RECORD_DSN = os.environ.get("TEST_RECORD_DB_URI")
pytestmark_db = pytest.mark.skipif(
    not _RECORD_DSN,
    reason="TEST_RECORD_DB_URI not set — no Postgres carrying the record migrations")


@pytestmark_db
def test_sweep_finds_a_born_uncut_graph_and_not_the_cut_over_one() -> None:
    """P1-1's mitigation, MEASURED: the hazard has to be findable.

    Two graphs of one org: A has journal rows and a counter row (cut over), B has
    journal rows and NO counter row (born uncut — the state a graph created after
    the cutover is in). A read-only sweep over the org must name B and only B, and
    must state that the floor is the JOURNAL's count rather than `row_max`.
    """
    import contextlib
    import uuid

    import psycopg2

    from tests.test_9078_record_written_by_production_path import (
        _app_role_dsn,
        _ensure_metering_schema,
        _ensure_record_schema,
    )

    admin = psycopg2.connect(_RECORD_DSN)
    admin.autocommit = True
    try:
        _ensure_record_schema(admin)
        _ensure_metering_schema(admin)
        _ensure_record_schema(admin)
        token = uuid.uuid4().hex[:10]
        org = f"org-13043-{token}"
        cut, uncut = f"graph-cut-{token}", f"graph-uncut-{token}"
        with admin.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                        (org, org))
            for graph in (cut, uncut):
                cur.execute(
                    "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s)",
                    (graph, org, graph))
                cur.execute(
                    "INSERT INTO public.journal_event "
                    "(org_id, graph_id, seq, event_type, payload) "
                    "VALUES (%s, %s, 1, 'PointAdded', %s)",
                    (org, graph, '{"id": "p"}'))
            cur.execute(
                "INSERT INTO public.record_cursor (org_id, graph_id, seq, mode) "
                "VALUES (%s, %s, 1, 'active')", (org, cut))

        app = psycopg2.connect(_app_role_dsn(_RECORD_DSN))
        app.autocommit = False
        try:
            payload = cli._cmd_sweep(
                app, type("A", (), {"org_id": org, "floor": None})())
        finally:
            with contextlib.suppress(Exception):
                app.rollback()
            app.close()
    finally:
        with contextlib.suppress(Exception):
            admin.close()

    assert [entry["graph_id"] for entry in payload["uncut_graphs"]] == [uncut], (
        f"the sweep did not isolate the born-uncut graph: {payload}")
    # ⛔ THE NUMBER ITSELF (#13043 review finding B, MEASURED): the per-graph read
    # ran AFTER a `conn.rollback()` had ended the transaction that carried the
    # transaction-local tenant GUC, so every entry reported `0` for a journal
    # holding one line. Asserting only the graph LIST — which is what this leg
    # did — left the fabricated floor in place.
    assert payload["uncut_graphs"][0]["journal_max_seq"] == 1, (
        f"the sweep fabricated the journal's max seq: {payload['uncut_graphs']}")
    assert payload["floor_is_the_journals_count"] is None, (
        "the sweep claimed its MAX(seq) IS the journal's enumeration — no "
        f"enumeration was read: {payload}")
    assert "floor_is_the_journals_count_reason" in payload, (
        "the sweep answered `null` to the floor question without saying why")
    assert "cannot_see" in payload, (
        "the sweep does not state what it cannot see — a graph with no journal "
        "row is invisible, and silence reads as coverage")


# ── the sweep's readings, driven on a fake (the DB leg above SKIPS everywhere
#    except `test-record-postgres`, so the properties it protects need a home
#    that runs on every lane) ───────────────────────────────────────────────

class _SweepCursor:
    """Answers the sweep's two queries and RECORDS when each one ran."""

    def __init__(self, conn):
        self.conn = conn
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        if "set_config" in sql:
            self._rows = []                 # the GUC write itself returns nothing used
        elif "pg_roles" in sql:            # the scope READ-BACK: (tenant, bypassing)
            self._rows = [(self.conn.org, False)]
        elif "IS NULL" in sql:            # UNCUT_GRAPHS_SQL
            # ⛔ TWO COLUMNS: `(graph_id, counter_mode)` (#14624 review 1). The
            # fake answers the statement the code ACTUALLY runs — a fake left on
            # the old one-column shape raises `IndexError` here instead of
            # reporting `counter_mode: None` for a seeded graph.
            self._rows = [(g, self.conn.modes.get(g)) for g in self.conn.graphs]
            self.conn.rollbacks_at_uncut_read = self.conn.rollbacks
        elif "COUNT(*)" in sql:            # JOURNAL_COUNT_SQL
            self.conn.count_reads.append((params[1], self.conn.rollbacks))
            self._rows = [(self.conn.count,)]
        else:                             # JOURNAL_MAX_SQL
            self.conn.max_reads.append((params[1], self.conn.rollbacks))
            self._rows = [(self.conn.max_seq,)]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _SweepConn:
    closed = False

    def __init__(self, graphs, max_seq=1, org="o", count=None, modes=None):
        self.graphs, self.max_seq, self.org = graphs, max_seq, org
        #: `graph_id -> counter_mode`, with a MISSING key meaning "no counter row"
        #: (`None`) — the born-uncut state. Set it to give a graph the `seeded` /
        #: `halted` row the population now also carries (#14624 review 1).
        self.modes = modes or {}
        # `count` defaults to `max_seq` — a DENSE journal, where the two
        # quantities coincide. Tests that care about the difference set it.
        self.count = max_seq if count is None else count
        self.rollbacks = 0
        self.rollbacks_at_uncut_read: int | None = None
        self.max_reads: list[tuple] = []
        self.count_reads: list[tuple] = []

    def cursor(self):
        return _SweepCursor(self)

    def rollback(self):
        self.rollbacks += 1


def _sweep(args, conn):
    return cli._cmd_sweep(conn, args)


def test_the_sweep_keeps_the_tenant_guc_alive_until_the_per_graph_read() -> None:
    """⛔ FINDING B, DB-FREE: the tenant GUC is TRANSACTION-LOCAL.

    `set_config('app.current_org_id', %s, true)` applies "only until the current
    transaction ends, whether it commits or rolls back" (PostgreSQL `SET`
    reference), and `journal_event_app_tenant` filters on
    `current_setting('app.current_org_id', true)`. So a `conn.rollback()` BETWEEN
    the scope and the per-graph `MAX(seq)` read makes every entry report `0` —
    the pre-flight fabricating the one datum it exists to report.

    The mutant this reds: re-adding `conn.rollback()` after the graph list is
    read (the shipped shape before this fix).
    """
    conn = _SweepConn(["g-a", "g-b"], max_seq=7)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": None,
                                    "journal": None})(), conn)
    assert [e["journal_max_seq"] for e in payload["uncut_graphs"]] == [7, 7]
    assert [e["journal_count"] for e in payload["uncut_graphs"]] == [7, 7]
    assert all(read[1] == 0 for read in conn.max_reads), (
        "a rollback ran between the tenant scope and the per-graph read — the "
        f"transaction-local GUC is gone there, so MAX(seq) answers 0: {conn.max_reads}")
    # …and the same for the COUNT read, which is a SECOND scoped statement: a
    # rollback between the two would make it answer 0 under a NULL predicate.
    assert conn.count_reads and all(read[1] == 0 for read in conn.count_reads), (
        f"the COUNT(*) read ran outside the tenant scope: {conn.count_reads}")


def test_the_sweep_reports_which_not_cut_over_state_each_graph_is_in() -> None:
    """⛔ THE POPULATION IS "NOT ALLOCATING", AND THE TWO STATES HAVE OPPOSITE
    REMEDIES (#14624 review 1).

    `backfill` without `--arm` leaves graphs `seeded`, which allocates nothing;
    a `halted` graph allocates nothing either. Both must stay in the pre-flight's
    population — omitting them made `sweep` answer **0 rows, "clean"** over
    graphs that refuse every record write, which is the false CLEAN the operator
    checklist then acted on. Because `seed`/`arm` heals the first and `reopen`
    the second, the mode travels with the id rather than being assumed from
    membership.

    The mutant this reds: drop `counter_mode` from the entry (KeyError), or
    narrow `UNCUT_GRAPHS_SQL` back to `c.graph_id IS NULL` (the DB legs and the
    static pin in `tests/test_14582_creation_hook.py` red for that half).
    """
    conn = _SweepConn(["g-seeded", "g-halted", "g-born"], max_seq=4,
                      modes={"g-seeded": "seeded", "g-halted": "halted"})
    payload = _sweep(type("A", (), {"org_id": "o", "floor": None,
                                    "journal": None})(), conn)
    assert payload["count"] == 3, payload
    assert {e["graph_id"]: e["counter_mode"] for e in payload["uncut_graphs"]} == {
        "g-seeded": "seeded", "g-halted": "halted", "g-born": None}, payload
    assert "allocating" in payload["count_means"] and "seeded" in payload["count_means"], (
        f"the payload must say what `count: 0` means: {payload.get('count_means')!r}")


def test_the_sweep_does_not_assert_a_floor_it_never_counted() -> None:
    """FINDING 4: `floor_is_the_journals_count: True` was a LITERAL.

    The number beside it is `MAX(seq)`, which the verb's own docstring says is
    NOT the floor; it differs whenever the seq axis has a gap. No enumeration
    was read, so the only honest answer is `None` + the reason.
    """
    conn = _SweepConn(["g"], max_seq=5)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": None,
                                    "journal": None})(), conn)
    entry = payload["uncut_graphs"][0]
    assert entry["journal_max_seq"] == 5, (
        "the entry is not labelled as the MAX(seq) it actually read")
    assert "journal_row_max" not in entry, (
        "the entry still calls MAX(seq) the journal's row max")
    assert entry["floor_is_the_journals_count"] is None
    assert payload["floor_is_the_journals_count"] is None
    reason = payload["floor_is_the_journals_count_reason"]
    assert reason, "the sweep answered `null` without saying why"
    # ⛔ THE REASON MUST NOT DISOWN THE NUMBER IN ITS OWN ENTRY (#13043 review P2).
    # `journal_count` is read and emitted unconditionally in `_cmd_sweep` — the
    # `JOURNAL_COUNT_SQL` read, and the entry's `"journal_count"` key, which the
    # assertion below pins — but the reason
    # used to say "`sweep` reads MAX(seq), not the journal's enumeration — pass
    # --journal … to have the count reported beside it": an operator was told to
    # re-run for a count the payload already carried, in the verb whose whole
    # purpose is that its own output can be trusted. What is unknown is the
    # COMPARISON (no floor was supplied), not the count.
    assert entry["journal_count"] == 5, (
        "the count is not in the entry, so the reason below would be honest")
    assert "journal_count" in reason, (
        f"the reason does not name the count it already reports: {reason!r}")
    assert "have the count reported" not in reason, (
        "the reason tells the operator to re-run for a count the payload "
        f"already carries: {reason!r}")


def test_the_null_floor_flag_states_the_cause_it_actually_had() -> None:
    """⛔ RE-REVIEW P2: the reason named a read the sweep DOES perform.

    The `null` above has exactly one cause — no floor was handed in, so there
    was nothing to compare — and that is what an operator needs told. The
    shipped sentence said "`sweep` reads MAX(seq), not the journal's
    enumeration — pass --journal … to have the count reported beside it", whose
    premise is false: the verb reads `COUNT(*)` per graph and already reports it
    as `journal_count`, so there is no count left for a flag to make it report.
    (`--journal` IS a working remedy — `main` counts it into `args.floor` — but
    what it supplies is the floor to compare against, not the count's
    reporting.)

    The mutant this reds: restoring that sentence (reason does not contain
    "no floor was supplied"). Non-vacuity: the entry really does carry the
    count the reason points the operator at.
    """
    conn = _SweepConn(["g"], max_seq=5)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": None,
                                    "journal": None})(), conn)
    reason = payload["floor_is_the_journals_count_reason"]
    assert "no floor was supplied" in reason, (
        "the reason does not name the cause the flag actually had (the sentence "
        f"names MAX(seq) and a --journal remedy instead): {reason!r}")
    assert "reads MAX(seq), not the journal's enumeration" not in reason, (
        f"the reason still claims the sweep does not read the count: {reason!r}")
    assert payload["uncut_graphs"][0]["journal_count"] == 5, (
        "the reason points at `journal_count`, but the entry does not carry it: "
        f"{payload['uncut_graphs'][0]}")
    assert "journal_count" in reason


def test_a_sweep_with_no_uncut_graph_does_not_blame_a_missing_floor() -> None:
    """The OTHER cause of the same `null`: with no uncut graph there is no
    journal to count, so a "pass --enumerated-floor" remedy would be wrong too.

    ⛔ And the absence is stated as what this verb READ, not as an org-wide fact:
    `sweep` sees `journal_event` rows only, so a never-written graph is
    invisible (the payload's own `cannot_see`).

    Mutant: drop the `if not entries` branch → this reds on "no born-uncut graph".
    """
    conn = _SweepConn([], max_seq=5)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": None,
                                    "journal": None})(), conn)
    assert payload["uncut_graphs"] == []
    reason = payload["floor_is_the_journals_count_reason"]
    assert "no born-uncut graph the record can see" in reason, (
        f"the empty sweep over-claims an absence it did not read: {reason!r}")
    assert "cannot_see" in reason, (
        f"the reason does not name the blindness that lets this happen: {reason!r}")


def test_the_sweep_reports_the_count_only_when_it_has_one() -> None:
    """Non-vacuity for the row above: with an enumeration it DOES answer."""
    conn = _SweepConn(["g"], max_seq=5)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": 5,
                                    "journal": "j.jsonl"})(), conn)
    entry = payload["uncut_graphs"][0]
    assert entry["enumerated_floor"] == 5 and entry["floor_source"] == "journal"
    assert entry["floor_is_the_journals_count"] is True
    # …and a MAX(seq) that DISAGREES with the count is reported as a disagreement,
    # which is the whole reason the claim cannot be a literal.
    conn = _SweepConn(["g"], max_seq=9)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": 5,
                                    "journal": "j.jsonl"})(), conn)
    assert payload["uncut_graphs"][0]["floor_is_the_journals_count"] is False


def test_the_floor_is_compared_with_the_journals_COUNT_not_with_MAX_seq() -> None:
    """⛔ ROUND-3 BLOCKING: the field says COUNT and the code compared `MAX(seq)`.

    The two are different quantities and they diverge exactly on the state this
    verb exists to find — a refused seq, which leaves a GAP (`count < max`).
    That is the ONLY direction that can exist: `public.journal_event` carries
    `PRIMARY KEY (org_id, graph_id, seq)` and `CHECK (seq > 0)`, so `count > max`
    is unrepresentable (N distinct positive seqs with maximum M satisfy N <= M)
    and deleting the tail lowers COUNT and MAX together (#13043 review P3 — this
    test previously captioned the reverse as "a lost tail"). Comparing the floor
    with `MAX(seq)` therefore answers the wrong question and, because a DENSE
    journal makes the two coincide, it can be wrong in the one direction that
    matters: reporting a false CLEAN.

    The mutant this reds, in both directions: `args.floor == journal_count`
    back to `args.floor == entry["journal_max_seq"]`.
    """
    # A GAP after a refused seq: the journal holds 5 lines, the highest is 9.
    # `--journal` counted 5, so the floor IS the count — pre-fix said False.
    conn = _SweepConn(["g"], max_seq=9, count=5)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": 5,
                                    "journal": "j.jsonl"})(), conn)
    entry = payload["uncut_graphs"][0]
    assert (entry["journal_count"], entry["journal_max_seq"]) == (5, 9), (
        "both numbers must be in the output, so neither stands in for the other")
    assert entry["floor_is_the_journals_count"] is True, (
        "a floor equal to the journal's COUNT was reported as a disagreement — "
        "the comparison used MAX(seq)")
    assert payload["floor_is_the_journals_count"] is True

    # The other direction, and the reason the comparison is not merely a
    # re-spelling of MAX(seq): a GAP higher up. 7 lines, highest is 9 — and the
    # operator's stated floor is 9, which IS `MAX(seq)`.
    #
    # ⛔ THE FLOOR EQUALS THE MAX HERE ON PURPOSE. Pre-fix (`args.floor ==
    # entry["journal_max_seq"]`) this state reads TRUE; post-fix (`== journal_count`)
    # it reads FALSE.
    #
    # The earlier fixture here was `count=9, max=5`, captioned "a lost/reused
    # tail" — it DID discriminate (measured: the mutant answers True), but the
    # state it models is forbidden by `PRIMARY KEY (org_id, graph_id, seq)` +
    # `CHECK (seq > 0)`, under which 9 distinct positive seqs cannot have a
    # maximum of 5 (#13043 review P3: "valid discriminator, wrong rationale").
    # A leg whose fixture cannot exist proves a property of an unreachable state,
    # so it is retargeted at the gap direction — the only one that does.
    conn = _SweepConn(["g"], max_seq=9, count=7)
    payload = _sweep(type("A", (), {"org_id": "o", "floor": 9,
                                    "journal": None})(), conn)
    entry = payload["uncut_graphs"][0]
    assert entry["floor_source"] == "operator"
    assert (entry["journal_count"], entry["journal_max_seq"]) == (7, 9)
    assert entry["floor_is_the_journals_count"] is False, (
        "a floor of 9 on a 7-line journal was reported as the count — the "
        "comparison used MAX(seq)")


def test_the_sweep_refuses_a_floor_it_cannot_attribute_to_one_graph() -> None:
    """FINDING 4, second half: `--enumerated-floor` was attached to EVERY entry.

    The comment beside it already claimed the verb "never applies it to more
    than" one graph, and the code applied it to all of them — and to none when
    the sweep found nothing. A floor is PER GRAPH, so a multi-graph sweep with
    one cannot be answered.
    """
    conn = _SweepConn(["g-a", "g-b"], max_seq=1)
    with pytest.raises(ValueError, match="PER GRAPH"):
        _sweep(type("A", (), {"org_id": "o", "floor": 3,
                              "journal": None})(), conn)
    # Zero graphs is the same defect: a floor for a graph that was not found.
    with pytest.raises(ValueError, match="PER GRAPH"):
        _sweep(type("A", (), {"org_id": "o", "floor": 3,
                              "journal": None})(), _SweepConn([], max_seq=1))


# ── P2-12: the wiring gate must SEE the flag read ─────────────────────────

@pytest.fixture(scope="module")
def wiring_mod():
    spec = importlib.util.spec_from_file_location(
        "record_wiring_check", _TOOLS / "record_wiring_check.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_real_tree_reads_the_flag_on_the_door_path(wiring_mod) -> None:
    assert wiring_mod.the_door_reads_the_allocate_flag() == [], (
        "the wiring gate reports the flag as unread on this tree")


_MUTATIONS = {
    # name: (old, new) — each is a way the flag's plumbing has been (or could
    # be) deleted while every OTHER claim of the gate still holds.
    "helper-deleted": (
        "def record_live_allocate() -> bool:",
        "def _renamed_record_live_allocate() -> bool:"),
    "helper-answers-without-the-env": (
        "    return env_flag(RECORD_ALLOCATE_ENV, False)",
        "    return False"),
    "door-never-binds-it": (
        "    allocate = allocate or record_live_allocate()",
        "    allocate = allocate"),
    # ⛔ FINDING V2, the FALSE PASS a reviewer MEASURED: the binding is kept and
    # only the FORWARDING is dropped, so `allocate=allocate` becomes
    # `allocate=False` at the door's `write_records(...)` call. Before this
    # mutant was added, the gate answered `[]` (WIRED) for it — a switch that
    # does nothing, certified.
    "door-binds-it-but-never-forwards-it": (
        "                              allocate=allocate)",
        "                              allocate=False)"),
    "constant-deleted": (
        'RECORD_ALLOCATE_ENV = "TORTOISE_RECORD_ALLOCATE"',
        'DEFAULT_ALLOCATE_ENV = "TORTOISE_RECORD_ALLOCATE"'),
    "not-exported": (
        '    "RECORD_ALLOCATE_ENV",\n',
        ""),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_the_wiring_gate_reds_when_the_flag_plumbing_is_deleted(
        wiring_mod, tmp_path, name) -> None:
    """⛔ THE MEASURED DEFECT: the gate certified WIRED over exactly this.

    Each mutation is applied to a COPY of the real module and handed to the rule,
    so the tree under test is never touched. Every one of these leaves the
    position-provenance claims intact — which is why nothing caught it.
    """
    old, new = _MUTATIONS[name]
    source = (Path(wiring_mod.__file__).resolve().parent.parent
              / "tortoise" / "record_writer.py").read_text()
    assert old in source, (
        f"the mutation {name!r} no longer matches the tree — it has drifted and "
        "must be re-pointed at the code it describes")
    mutated = tmp_path / "record_writer.py"
    mutated.write_text(source.replace(old, new, 1))
    problems = wiring_mod.the_door_reads_the_allocate_flag(mutated)
    assert problems, (
        f"{name}: the wiring gate passed a tree whose allocate flag is not read "
        "on the door's path — this is the P2-12 defect restored")
